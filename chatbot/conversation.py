"""
chatbot/conversation.py - מוח הצ'אטבוט: מנהל את מצב השיחה ומממש את כל מה שהבוט יודע לעשות -
זיהוי, אימות תעודת זהות, מסירת פרטי התור, קביעת תור חדש, ביטול תור, ורישום לקוח/ה חדש/ה.

כל הפונקציות כאן מקבלות ומחזירות את מצב השיחה (state) כ-dict רגיל של פייתון - אין כאן שום
תלות ב-Flask עצמו. routes/chatbot_routes.py הוא זה ששומר את ה-dict הזה בפועל בתוך session
של Flask, בין הודעה להודעה. ההפרדה הזו הופכת את לוגיקת השיחה לקלה יותר להבנה ולבדיקה.

==========================================================================================
גבולות ההרשאה של הבוט - שלושה כללים קשיחים, שנאכפים במבנה הקוד ולא רק "בכוונה טובה":

  1. אסור לחשוף פרט אישי לפני אימות תעודת זהות מוצלח.
     אכיפה: כל גישה למידע על תורים עוברת דרך _load_verified_appointment, שמתחילה בקריאה
     ל-_assert_verified. אם השיחה אינה במצב STAGE_VERIFIED - נזרקת שגיאה במקום להחזיר מידע.

  2. אסור לבצע פעולה כלשהי (קביעת תור, ביטול תור) בלי אימות ובלי בקשה מפורשת.
     אכיפה: _book_appointment ו-_cancel_appointment מתחילות שתיהן ב-_assert_verified,
     ושתיהן נקראות אך ורק אחרי שהמשתמש/ת אישר/ה במפורש סיכום פעולה (STAGE_*_CONFIRM).

  3. אסור לבצע פעולות שרק מנהל/ת רשאי/ת להן, ואסור למסור נתונים מבסיס הנתונים.
     אכיפה: הקובץ הזה מייבא אך ורק את models.customers ו-models.appointments, ומשתמש בהם
     רק בפונקציות שמסננות ללקוח/ה אחד/ת - זה שאומת/ה בשיחה. אין כאן שום ייבוא של
     models.invoices, models.leads או models.reports, ואין שום קריאה שמחזירה רשימות של
     לקוחות או של תורים של אחרים. בקשה כזו מזוהה כ-intent 'admin_request' ונענית בסירוב.
==========================================================================================
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import datetime   # לבדיקת תאריכים בעבר
import re         # לזיהוי מילות כן/לא ולחילוץ ספרות מתוך הודעות המשתמש

import config                          # קובץ ההגדרות (מספר ניסיונות האימות המותר, שעות פעילות)
import models.customers as customers     # חיפוש לקוח לפי שם/ת.ז., שליפת פרטי לקוח, יצירת לקוח חדש
import models.appointments as appointments  # שליפת/יצירת/מחיקת תורים - הכל מסונן ללקוח/ה שאומת/ה בלבד
import chatbot.nlu as nlu                     # חילוץ הפרטים מהודעה חופשית (Gemini)
import chatbot.gemini_client as gemini_client   # ניסוח התשובה הסופית בשפה טבעית (Gemini)

# ---- שלבי השיחה האפשריים ("מכונת מצבים") ----
STAGE_START = "start"                     # עדיין לא ידוע מי המשתמש/ת
STAGE_AWAIT_CONFIRM = "await_confirm"     # נמצא לקוח יחיד תואם, מחכים לאישור "כן/לא זה אני"
STAGE_AWAIT_CLARIFY = "await_clarify"     # נמצאו כמה לקוחות תואמים, מחכים לשם מלא לצורך הבהרה
STAGE_AWAIT_ID = "await_id"               # מחכים להזנת תעודת זהות לצורך אימות
STAGE_VERIFIED = "verified"               # הזהות אומתה בהצלחה בשיחה הזו
STAGE_BLOCKED = "blocked"                 # נחסם אחרי יותר מדי ניסיונות אימות כושלים
STAGE_BOOKING = "booking"                 # אוספים את פרטי התור החדש (טיפולים/תאריך/שעה/מגדר)
STAGE_BOOKING_IDENTITY = "booking_identity"   # אוספים פרטי זיהוי כדי להשלים את ההזמנה
STAGE_BOOKING_CONFIRM = "booking_confirm"       # מציגים סיכום ומחכים לאישור מפורש לפני שקובעים
STAGE_CANCEL_CHOOSE = "cancel_choose"             # ללקוח/ה יש כמה תורים - שואלים איזה מהם לבטל
STAGE_CANCEL_CONFIRM = "cancel_confirm"             # מציגים את התור ומחכים לאישור מפורש לפני ביטול

_POSITIVE_WORDS = {"כן", "נכון", "אכן", "בדיוק", "כמובן", "מאשר", "מאשרת", "yes", "yep", "ok", "אוקיי"}
_NEGATIVE_WORDS = {"לא", "שלילי", "טעות", "בטל", "no", "nope"}

_BOOKING_HINT = "אפשר לקבוע תור כאן איתי בצ'אט - פשוט לכתוב 'אני רוצה לקבוע תור'."

_ADMIN_REFUSAL = (
    "אני העוזר/ת של הלקוחות, ואין לי הרשאה לפעולות ניהול או למסירת נתונים מהמערכת - "
    "כמו רשימות לקוחות, תורים של אנשים אחרים, חשבוניות, לידים או נתוני הכנסות. "
    "אני יכול/ה לעזור רק עם התור שלך: לבדוק אותו, לקבוע תור חדש או לבטל תור קיים."
)

_PHRASING_SYSTEM_INSTRUCTION = (
    "את/ה עוזר/ת קליניקת קוסמטיקה ידידותי/ת, שמנוסח/ת בעברית תקנית. תקבל/י עובדות אמיתיות על "
    "תור של לקוח/ה, ואת התאריך שהלקוח/ה חשב/ה (בטעות) שהתור נמצא בו. נסח/י משפט אחד או שניים "
    "קצרים, אדיבים וברורים בעברית, שמודיעים ללקוח/ה מה התאריך והשעה האמיתיים של התור (ואילו "
    "שירותים כלולים בו), ומציינים בעדינות שהתאריך שהוא/היא ציין/ה שגוי. "
    "חשוב מאוד: אסור לך להמציא, לשנות או לנחש שום נתון - יש להשתמש אך ורק בדיוק בעובדות שסופקו "
    "לך בהודעה. אל תוסיף/י ברכות מיותרות או הקדמות ארוכות - ישר לעניין."
)

_WEEKDAY_NAMES_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]   # לפי weekday() של פייתון


# ============================== ניהול מצב השיחה ==============================

def new_state():
    """יוצר מצב שיחה חדש וריק - נקודת ההתחלה, לפני שידוע מיהו הלקוח/ה."""
    return {
        "stage": STAGE_START,                                          # מתחילים תמיד משלב הזיהוי הראשוני
        "candidate_customer_id": None,                                     # עדיין אין מועמד/ת
        "identity_verified": False,                                          # דגל האימות - ראו _assert_verified
        "cancel_target_id": None,                                              # מזהה התור שנבחר לביטול
        "claimed_date": None,                                                # עדיין אין תאריך נטען
        "attempts_left": config.CHATBOT_MAX_VERIFICATION_ATTEMPTS,             # מספר ניסיונות האימות המלא
        "booking": _empty_booking(),                                             # טיוטת ההזמנה (ריקה)
        "registration": _empty_registration(),                                     # פרטי רישום לקוח/ה חדש/ה
        "history": [],                                                               # תמלול השיחה, להצגה במסך
    }


def _empty_booking():
    """טיוטת הזמנה ריקה: מה שנאסף עד כה עבור תור חדש."""
    return {"service_ids": [], "service_names": [], "date": None, "time": None, "gender": None}


def _empty_registration():
    """פרטי רישום ריקים: מה שנאסף עד כה עבור לקוח/ה שעדיין לא רשום/ה במערכת."""
    return {"full_name": None, "phone": None, "id_number": None}


def _assert_verified(state):
    """שומר הסף של כללי ההרשאה: מוודא שהשיחה באמת עברה אימות תעודת זהות מוצלח, לפני שניגשים
    למידע אישי או מבצעים פעולה כלשהי בשם הלקוח/ה. אם לא - זורק שגיאה במקום להמשיך. זו הגנה
    מבנית מפני באג עתידי: עדיף שהמערכת תיפול בקול רם מאשר תדלוף מידע או תבצע פעולה לא מורשית."""
    # הבדיקה היא על הדגל identity_verified בלבד, ולא על שלב השיחה או על קיום candidate_customer_id.
    # הסיבה: candidate_customer_id נקבע כבר בשלב הצעת השם (לפני שהוזנה תעודת זהות), ולכן הוא
    # *אינו* הוכחה לאימות. הדגל הזה נדלק אך ורק בשתי נקודות בקוד: אחרי התאמת תעודת זהות מדויקת,
    # ואחרי רישום לקוח/ה חדש/ה שמסר/ה בעצמו/ה את פרטיו. זו נקודת האמת היחידה.
    if not state.get("identity_verified") or not state.get("candidate_customer_id"):
        raise PermissionError("ניסיון לגשת למידע אישי או לבצע פעולה לפני אימות זהות - נחסם")


def handle_message(state, user_message):
    """נקודת הכניסה המרכזית: מקבלת את מצב השיחה ואת ההודעה החדשה, ומחזירה (state מעודכן, תשובה)."""
    user_message = (user_message or "").strip()                        # ניקוי רווחים מיותרים בקצוות
    if not user_message:                                                  # הודעה ריקה - אין מה לעבד
        return state, "לא קיבלתי שום הודעה. אפשר לכתוב שוב?"

    _ensure_state_shape(state)                                          # תאימות לשיחות שנפתחו בגרסה קודמת
    state["history"].append({"role": "user", "text": user_message})       # שמירת הודעת המשתמש בתמלול

    stage = state["stage"]                                               # השלב הנוכחי של השיחה
    if stage == STAGE_BLOCKED:
        reply = ("השיחה הזו נחסמה בעקבות יותר מדי ניסיונות אימות שגויים, מטעמי אבטחה. "
                 "אפשר לפנות אלינו טלפונית, או ללחוץ על 'שיחה חדשה' כדי להתחיל מחדש.")
    elif stage == STAGE_START:
        reply = _handle_start(state, user_message)
    elif stage == STAGE_AWAIT_CONFIRM:
        reply = _handle_await_confirm(state, user_message)
    elif stage == STAGE_AWAIT_CLARIFY:
        reply = _handle_await_clarify(state, user_message)
    elif stage == STAGE_AWAIT_ID:
        reply = _handle_await_id(state, user_message)
    elif stage == STAGE_BOOKING:
        reply = _handle_booking(state, user_message)
    elif stage == STAGE_BOOKING_IDENTITY:
        reply = _handle_booking_identity(state, user_message)
    elif stage == STAGE_BOOKING_CONFIRM:
        reply = _handle_booking_confirm(state, user_message)
    elif stage == STAGE_CANCEL_CHOOSE:
        reply = _handle_cancel_choose(state, user_message)
    elif stage == STAGE_CANCEL_CONFIRM:
        reply = _handle_cancel_confirm(state, user_message)
    elif stage == STAGE_VERIFIED:
        reply = _handle_verified(state, user_message)
    else:                                                                  # מצב לא צפוי (הגנה תיאורטית)
        reply = "משהו השתבש. אפשר ללחוץ על 'שיחה חדשה' ולנסות שוב?"

    state["history"].append({"role": "bot", "text": reply})             # שמירת תשובת הבוט בתמלול
    return state, reply


def _ensure_state_shape(state):
    """מוסיף שדות חדשים ל-state של שיחה שנפתחה בגרסה קודמת של הבוט (ושמורה ב-session של הדפדפן),
    כדי שהיא לא תקרוס אחרי עדכון גרסה."""
    state.setdefault("booking", _empty_booking())
    state.setdefault("registration", _empty_registration())
    state.setdefault("identity_verified", False)
    state.setdefault("cancel_target_id", None)
    state.setdefault("claimed_date", None)
    state.setdefault("attempts_left", config.CHATBOT_MAX_VERIFICATION_ATTEMPTS)


# ============================== כלי עזר קטנים ==============================

def _yes_no_intent(text):
    """מזהה תשובה חיובית/שלילית לפי מילים שלמות בלבד (לא תת-מחרוזת), בלי לבזבז קריאת API."""
    words = re.findall(r"[\w֐-׿]+", text.strip().lower())
    if any(word in _POSITIVE_WORDS for word in words):
        return "positive"
    if any(word in _NEGATIVE_WORDS for word in words):
        return "negative"
    return None


def _digits_only(text):
    """משאיר רק ספרות ממחרוזת - להשוואת תעודות זהות בלי להיתקע על רווחים/מקפים."""
    return re.sub(r"\D", "", text or "")


def _looks_like_id_attempt(text):
    """האם ההודעה היא בכלל ניסיון להזין תעודת זהות. חשוב להוגנות: שאלה תמימה לא תבזבז ניסיון."""
    return len(_digits_only(text)) >= 5


def _name_matches(given_name, full_name):
    """בודק אם השם שנמסר מתיישב עם השם הרשום במערכת (לפחות מילה משותפת אחת)."""
    given_words = set(re.findall(r"[\w֐-׿]+", (given_name or "").lower()))
    full_words = set(re.findall(r"[\w֐-׿]+", (full_name or "").lower()))
    return bool(given_words & full_words)


def _format_date_he(iso_date):
    """ממיר תאריך מפורמט בסיס הנתונים (2026-10-05) לפורמט קריא בעברית (05.10.2026)."""
    parts = (iso_date or "").split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else iso_date


def _format_appointment_details(appt):
    """בונה תיאור קריא של תור: תאריך, שעה ורשימת השירותים."""
    services = appt.get("service_names") or "לא צוינו שירותים"
    return f"{_format_date_he(appt['appointment_date'])} בשעה {appt['appointment_time']} (שירותים: {services})"


def _service_names_list():
    """מחזיר את רשימת שמות הטיפולים של הקליניקה - להזרקה להוראת המערכת של ה-NLU."""
    return [service["name"] for service in appointments.list_services()]


def _extract(user_message):
    """עוטף את קריאת ה-NLU, ומזריק לה את רשימת הטיפולים האמיתית של הקליניקה."""
    return nlu.extract(user_message, available_service_names=_service_names_list())


# ============================== תשובות למידע ציבורי ==============================
# מידע פומבי בלבד: קטלוג הטיפולים ושעות הפעילות, שמוצגים ממילא לכל אחד באתר. אין כאן שום
# נתון שקשור ללקוח/ה כלשהו/י, ולכן מותר להשיב על השאלות האלה בכל שלב, גם לפני אימות.

def _services_answer():
    """רשימת הטיפולים של הקליניקה, כולל משך ומחיר - קטלוג פומבי."""
    service_list = appointments.list_services()
    if not service_list:
        return "רשימת הטיפולים אינה זמינה כרגע."
    lines = [f"• {s['name']} - {s['default_duration_minutes']} דקות, {int(s['default_price'])} ש\"ח"
             for s in service_list]
    return "אלה הטיפולים שאנחנו מציעים:\n" + "\n".join(lines)


def _hours_answer():
    """שעות וימי הפעילות של הקליניקה - מידע פומבי מקובץ ההגדרות."""
    open_days = ", ".join(_WEEKDAY_NAMES_HE[day] for day in config.OPEN_WEEKDAYS)
    return f"אנחנו פתוחים בימים {open_days}, בין השעות {config.OPENING_TIME} ל-{config.CLOSING_TIME}."


def _public_answer(intent):
    """מחזיר תשובה לשאלה 'ציבורית', או None אם הכוונה אינה כזו."""
    if intent == nlu.INTENT_SERVICE_INFO:
        return _services_answer()
    if intent == nlu.INTENT_OPENING_HOURS:
        return _hours_answer()
    if intent == nlu.INTENT_GREETING:
        return ("שלום! אני העוזר/ת הדיגיטלי/ת של הקליניקה. אפשר לבקש ממני לבדוק מתי התור שלך, "
                "לקבוע תור חדש או לבטל תור קיים (אבקש אימות זהות לפני כן), "
                "וגם לשאול על מחירי טיפולים ושעות פתיחה.")
    if intent == nlu.INTENT_ADMIN_REQUEST:
        return _ADMIN_REFUSAL
    return None


# ============================== שלב 1: זיהוי ראשוני ==============================

def _handle_start(state, user_message):
    """שלב הזיהוי הראשוני: חילוץ הפרטים מההודעה, וניתוב לזרימה המתאימה."""
    # מסלול מהיר: הודעה שכולה ספרות היא בבירור תעודת זהות - אין שום שפה להבין, ולכן מדלגים
    # לחלוטין על הקריאה ל-Gemini. התשובה מיידית, ונחסכת קריאת API מהמכסה החינמית.
    stripped = user_message.strip()
    if stripped.isdigit() and len(stripped) >= 5:
        return _verify_with_id_number(state, stripped)

    extracted = _extract(user_message)
    if extracted is None:
        return ("מצטערים, לא הצלחתי להבין את ההודעה כרגע. אפשר לנסח מחדש? "
                "לדוגמה: 'קוראים לי דנה ויש לי תור ב-15.03.2027'.")

    return _route_extracted(state, extracted)


def _route_extracted(state, extracted):
    """מנתב לפי מה שחולץ מההודעה - משותף לשלב ההתחלה ולשלבים אחרים שנופלים חזרה לזיהוי."""
    intent = extracted["intent"]
    if extracted["claimed_date"]:
        state["claimed_date"] = extracted["claimed_date"]     # תאריך של תור קיים שהלקוח/ה טוען/ת

    _absorb_booking_details(state, extracted)                   # אם צוינו פרטי תור חדש - נשמור אותם

    if intent == nlu.INTENT_ADMIN_REQUEST:                      # בקשת פעולת ניהול - סירוב תמידי
        return _ADMIN_REFUSAL

    if intent == nlu.INTENT_BOOK_NEW:                           # בקשה מפורשת לקבוע תור חדש
        return _start_or_continue_booking(state)

    if intent == nlu.INTENT_CANCEL:                             # בקשה מפורשת לבטל תור
        if state["stage"] == STAGE_VERIFIED:
            return _offer_cancellation(state)
        return ("בשמחה. כדי לבטל תור אני צריך/ה קודם לאמת את זהותך. "
                "אפשר לכתוב את השם המלא שלך?")

    name, id_number = extracted["name"], extracted["id_number"]
    if not name and not id_number:                              # לא נמסר שום פרט מזהה
        public = _public_answer(intent)
        if public:
            return public
        return "כדי לבדוק את התור שלך אני צריך/ה לדעת מי את/ה. אפשר לכתוב את השם שלך?"

    if id_number:                                               # מסלול מהיר: נמסרה תעודת זהות
        return _verify_with_id_number(state, id_number, claimed_name=name)

    matches = customers.search_customers_by_name(name)          # חיפוש לקוחות תואמים לפי השם
    if len(matches) == 0:
        return (f"לא מצאתי לקוח/ה בשם '{name}' במערכת שלנו. אפשר לבדוק את האיות ולנסות שוב? "
                f"אם עוד לא היית/ה אצלנו - {_BOOKING_HINT}")
    if len(matches) == 1:
        return _propose_candidate(state, matches[0])

    state["stage"] = STAGE_AWAIT_CLARIFY                        # 2+ תוצאות - לא מנחשים, מבקשים הבהרה
    return f"מצאתי כמה לקוחות בשם '{name}' במערכת. אפשר לכתוב את השם המלא שלך (שם פרטי ושם משפחה)?"


def _propose_candidate(state, candidate):
    """שומר מועמד/ת יחיד/ה, ומבקש אישור על השם המלא לפני שעוברים לאימות."""
    state["candidate_customer_id"] = candidate["id"]     # רק המזהה נשמר ב-state, לא פרטים אישיים
    state["stage"] = STAGE_AWAIT_CONFIRM
    return f"קוראים לך {candidate['full_name']}?"


# ============================== שלב 2: הבהרה ואישור שם ==============================

def _handle_await_clarify(state, user_message):
    """המשתמש/ת אמור/ה לכתוב שם מלא, כדי לצמצם למועמד/ת יחיד/ה מתוך כמה התאמות."""
    matches = customers.search_customers_by_name(user_message)   # חיפוש ישיר, בלי לבזבז קריאת API
    if len(matches) == 1:
        return _propose_candidate(state, matches[0])
    if len(matches) > 1:
        return "עדיין נמצאו כמה התאמות. אפשר לכתוב שם מלא ומדויק יותר (שם פרטי ושם משפחה)?"

    extracted = _extract(user_message)                           # 0 התאמות - אולי זו בכלל שאלה
    if extracted:
        public = _public_answer(extracted["intent"])
        if public:
            return public + "\n\nובחזרה לזיהוי: אפשר לכתוב את השם המלא שלך?"
        if extracted["id_number"]:
            return _verify_with_id_number(state, extracted["id_number"], claimed_name=extracted["name"])
        if extracted["name"]:
            by_name = customers.search_customers_by_name(extracted["name"])
            if len(by_name) == 1:
                return _propose_candidate(state, by_name[0])
    return "עדיין לא הצלחתי למצוא לקוח/ה בשם הזה. אפשר לכתוב את השם המלא בדיוק כפי שהוא רשום אצלנו?"


def _handle_await_confirm(state, user_message):
    """המשתמש/ת מאשר/ת או מכחיש/ה שזה השם המלא שלו/ה, לפני שמבקשים תעודת זהות."""
    intent = _yes_no_intent(user_message)
    if intent == "positive":
        state["stage"] = STAGE_AWAIT_ID
        return "מה תעודת הזהות שלך? (לצורך אימות בלבד)"
    if intent == "negative":
        state["candidate_customer_id"] = None
        state["identity_verified"] = False     # ביטול המועמד/ת מכבה גם את דגל האימות
        state["stage"] = STAGE_START
        return "אין בעיה, מצטערים על הטעות. אפשר לכתוב מה השם המלא שלך?"

    if _looks_like_id_attempt(user_message):        # המשתמש/ת "קפץ/ה קדימה" והזין/ה ת.ז.
        state["stage"] = STAGE_AWAIT_ID
        return _handle_await_id(state, user_message)

    extracted = _extract(user_message)
    if extracted:
        public = _public_answer(extracted["intent"])
        if public:
            return public + "\n\nובחזרה לזיהוי: השם שהצגתי הוא הנכון? (כן / לא)"
        if extracted["name"]:
            matches = customers.search_customers_by_name(extracted["name"])
            if len(matches) == 1:
                return _propose_candidate(state, matches[0])
    return "לא הבנתי - זה נכון או לא נכון? אפשר לענות 'כן' או 'לא'."


# ============================== שלב 3: אימות תעודת זהות ==============================

def _spend_attempt(state):
    """מפחית ניסיון אימות אחד, ומחזיר True אם נגמרו הניסיונות (כלומר צריך לחסום את השיחה)."""
    state["attempts_left"] -= 1
    if state["attempts_left"] <= 0:
        state["stage"] = STAGE_BLOCKED
        state["candidate_customer_id"] = None      # מחיקת המועמד/ת מה-state, ליתר ביטחון
        state["identity_verified"] = False           # וכיבוי מוחלט של דגל האימות
        return True
    return False


def _blocked_message():
    """הודעת החסימה האחידה, אחרי ניצול כל ניסיונות האימות."""
    return ("תעודת הזהות שהוזנה שגויה, ונוצל מספר הניסיונות המרבי המותר. "
            "מטעמי אבטחה השיחה נחסמת כעת. אפשר לפנות אלינו טלפונית, או להתחיל שיחה חדשה.")


def _after_verification(state, customer):
    """נקרא מיד אחרי אימות מוצלח: אם הייתה בקשה שממתינה לאימות (קביעת תור/ביטול) - ממשיכים
    אליה. אחרת מציגים את פרטי התור הקיים, שזו ברירת המחדל."""
    state["stage"] = STAGE_VERIFIED
    state["identity_verified"] = True      # נקודת האמת: מכאן ואילך מותר מידע אישי ופעולות
    booking = state["booking"]
    if booking["service_ids"] or booking["date"] or booking["time"]:   # הייתה בקשה לקבוע תור
        return _start_or_continue_booking(state)
    return _build_verified_reply(state, customer)


def _verify_with_id_number(state, id_number, claimed_name=None):
    """אימות ישיר לפי תעודת זהות שנמסרה בהודעה חופשית. תעודת הזהות היא סוד שרק בעל/ת התעודה
    אמור/ה להכיר, ולכן התאמה מדויקת שלה מהווה אימות לגיטימי. כל ניסיון כושל צורך ניסיון
    מהמכסה, כדי שלא ניתן יהיה לנחש תעודות זהות באופן שיטתי."""
    customer = customers.find_customer_by_id_number(id_number)
    if customer is None:                                    # התעודה כלל לא רשומה במערכת
        if _spend_attempt(state):
            return _blocked_message()
        return ("לא נמצא/ה במערכת שלנו לקוח/ה עם תעודת הזהות הזו. "
                "אם זו הפעם הראשונה שלך אצלנו - נשמח לארח אותך! "
                f"{_BOOKING_HINT} אם את/ה בטוח/ה שכבר ביקרת אצלנו, אפשר לבדוק את המספר ולנסות שוב.")

    if claimed_name and not _name_matches(claimed_name, customer["full_name"]):
        # הגנת עומק: תעודת זהות אמיתית, אך בשם שאינו מתיישב עם הרשום עליה במערכת
        if _spend_attempt(state):
            return _blocked_message()
        return "הפרטים שנמסרו אינם מתיישבים זה עם זה. אפשר לבדוק את השם ואת מספר תעודת הזהות ולנסות שוב?"

    state["candidate_customer_id"] = customer["id"]     # מרגע זה זהו הלקוח/ה של השיחה
    return _after_verification(state, customer)


def _handle_await_id(state, user_message):
    """השוואת תעודת הזהות שהוזנה מול הערך השמור עבור המועמד/ת. השלב הקריטי מבחינת אבטחה."""
    candidate = customers.get_customer(state["candidate_customer_id"])
    if not candidate:                                     # הגנה (למשל אם הלקוח/ה נמחק/ה באמצע)
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        state["identity_verified"] = False
        return "משהו השתבש באיתור הפרטים שלך. אפשר להתחיל שוב ולכתוב את השם שלך?"

    if not _looks_like_id_attempt(user_message):          # ההודעה אינה ניסיון להזין תעודת זהות
        extracted = _extract(user_message)
        if extracted:
            public = _public_answer(extracted["intent"])
            if public:                                       # שאלה לגיטימית - עונים בלי לגבות ניסיון
                return public + "\n\nכדי להמשיך, אפשר להזין את מספר תעודת הזהות."
        # חשוב: לא מנצלים כאן ניסיון - המשתמש/ת בכלל לא ניסה/תה לנחש מספר
        return ("כדי להגן על הפרטיות שלך אני חייב/ת לאמת את זהותך לפני שאוכל למסור פרטים או "
                "לבצע פעולות. אפשר להזין את מספר תעודת הזהות (9 ספרות)?")

    entered_id = _digits_only(user_message)
    real_id = _digits_only(candidate.get("id_number") or "")

    if real_id and entered_id == real_id:                 # התאמה מדויקת - ורק זה מאפשר המשך
        return _after_verification(state, candidate)

    other_owner = customers.find_customer_by_id_number(entered_id)   # האם התעודה בכלל קיימת אצלנו?
    if _spend_attempt(state):
        return _blocked_message()

    remaining = state["attempts_left"]
    if other_owner is None:
        return (f"תעודת הזהות הזו אינה רשומה במערכת שלנו. "
                f"אם זו הפעם הראשונה שלך אצלנו - {_BOOKING_HINT} "
                f"אם כבר ביקרת אצלנו, אפשר לבדוק את המספר ולנסות שוב (נותרו {remaining} ניסיונות).")
    # התעודה קיימת אך שייכת ללקוח/ה אחר/ת - לא חושפים בשום אופן למי
    return (f"מספר תעודת הזהות שהוזן אינו תואם ללקוח/ה בשם {candidate['full_name']}. "
            f"נותרו {remaining} ניסיונות.")


# ============================== מסירת פרטי התור הקיים ==============================

def _load_verified_appointment(state):
    """הפונקציה היחידה שניגשת למידע על תורים. מתחילה בבדיקת _assert_verified, כך שגישה
    למידע אישי לפני אימות תיפול בשגיאה במקום לדלוף החוצה."""
    _assert_verified(state)
    return appointments.get_next_upcoming_appointment(state["candidate_customer_id"])


def _build_verified_reply(state, candidate):
    """נקראת אחרי אימות מוצלח: שולפת את התור האמיתי ומנסחת תשובה, כולל תיקון אם יש פער."""
    appt = _load_verified_appointment(state)
    claimed_date = state.get("claimed_date")

    if not appt:
        return (f"מצאתי אותך, {candidate['full_name']}! "
                f"לא נמצא לך כרגע אף תור עתידי פתוח במערכת. {_BOOKING_HINT}")

    if claimed_date and claimed_date != appt["appointment_date"]:      # פער בין הטענה למציאות
        phrased = _phrase_correction(candidate["full_name"], claimed_date, appt)
        if phrased:
            return phrased
        return (f"מצאתי אותך, {candidate['full_name']}! שימו לב: התור שלך בפועל הוא ב-"
                f"{_format_appointment_details(appt)} (ולא ב-{_format_date_he(claimed_date)} כפי שציינת).")

    return f"מצאתי אותך, {candidate['full_name']}! התור שלך הוא ב-{_format_appointment_details(appt)}."


def _phrase_correction(full_name, claimed_date, appt):
    """קריאה אחת ל-Gemini לניסוח טבעי של פער התאריכים - רק אחרי שכל הנתונים כבר בידינו.
    ההודעה המקורית של המשתמש/ת לא נשלחת לכאן בכלל, אלא רק עובדות מבסיס הנתונים - וכך לא ניתן
    להזריק דרך הצ'אט הוראות שישנו את התנהגות המודל בשלב הרגיש הזה."""
    facts = (f"שם הלקוח/ה: {full_name}. "
             f"התאריך שהלקוח/ה טען/ה: {_format_date_he(claimed_date)}. "
             f"התאריך האמיתי של התור: {_format_date_he(appt['appointment_date'])}. "
             f"השעה האמיתית: {appt['appointment_time']}. "
             f"השירותים בתור: {appt.get('service_names') or 'לא צוינו'}.")
    return gemini_client.generate_text(_PHRASING_SYSTEM_INSTRUCTION, facts)


# ============================== קביעת תור חדש ==============================
# זו פעולה שכותבת לבסיס הנתונים, ולכן היא כפופה לשני תנאים קשיחים:
#   (א) אימות זהות מוצלח (הדגל identity_verified) - נאכף ב-_book_appointment דרך _assert_verified.
#   (ב) אישור מפורש של המשתמש/ת על סיכום ההזמנה - שלב STAGE_BOOKING_CONFIRM.
# הבוט לעולם לא קובע תור מיוזמתו או בלי ששני התנאים התקיימו.

def _absorb_booking_details(state, extracted):
    """קולט לתוך טיוטת ההזמנה כל פרט שחולץ מההודעה (טיפולים/תאריך/שעה/מגדר). כך אפשר לומר
    הכל במשפט אחד ("לק ג'ל מחר ב-10:00") או לטפטף פרט-פרט - שתי הדרכים עובדות."""
    booking = state["booking"]

    if extracted.get("service_names"):                                   # אם הוזכרו שמות טיפולים
        name_to_service = {s["name"]: s for s in appointments.list_services()}   # מיפוי שם -> שירות
        chosen = [name_to_service[n] for n in extracted["service_names"] if n in name_to_service]
        if chosen:                                                          # רק אם נמצאה התאמה אמיתית בקטלוג
            booking["service_ids"] = [s["id"] for s in chosen]                 # שמירת המזהים לביצוע
            booking["service_names"] = [s["name"] for s in chosen]               # ושמות לתצוגה
            booking["time"] = None    # שינוי הטיפולים משנה את משך התור, ולכן השעה שנבחרה כבר לא בהכרח תקפה

    if extracted.get("requested_date"):
        booking["date"] = extracted["requested_date"]
        booking["time"] = None        # שינוי תאריך מאפס את השעה, כי המשבצות הפנויות שונות בכל יום
    if extracted.get("requested_time"):
        booking["time"] = extracted["requested_time"]
    if extracted.get("gender"):
        booking["gender"] = extracted["gender"]


def _booking_total(booking):
    """מחשב את המשך הכולל (בדקות) ואת המחיר הכולל של הטיפולים שנבחרו."""
    chosen = appointments.get_services_by_ids(booking["service_ids"])
    duration = sum(s["default_duration_minutes"] for s in chosen)
    price = sum(s["default_price"] for s in chosen)
    return duration, price


def _start_or_continue_booking(state):
    """הלב של זרימת ההזמנה: בודק מה עדיין חסר בטיוטה, ושואל על הפריט הבא בלבד. כשהכל מלא -
    עובר לזיהוי (אם צריך) ואז לסיכום לאישור."""
    booking = state["booking"]

    if not booking["service_ids"]:                                  # (1) אילו טיפולים
        state["stage"] = STAGE_BOOKING
        return "בשמחה! אילו טיפולים תרצה/י?\n" + _services_answer()

    if not booking["date"]:                                          # (2) באיזה תאריך
        state["stage"] = STAGE_BOOKING
        return (f"נבחרו: {', '.join(booking['service_names'])}. לאיזה תאריך תרצה/י לקבוע? "
                f"(אפשר לכתוב למשל 'מחר' או '15.03.2027')")

    date_problem = _validate_booking_date(booking["date"])           # ולידציה של התאריך שנבחר
    if date_problem:
        booking["date"] = None
        state["stage"] = STAGE_BOOKING
        return date_problem

    duration, _price = _booking_total(booking)
    if not booking["time"]:                                          # (3) באיזו שעה
        free_slots = appointments.get_free_slots(booking["date"], duration)
        state["stage"] = STAGE_BOOKING
        if not free_slots:
            booking["date"] = None
            return (f"מצטערים, אין משבצות פנויות ב-{_format_date_he(booking['date'])} "
                    f"עבור {duration} דקות טיפול. אפשר לנסות תאריך אחר?")
        return (f"אלה השעות הפנויות ב-{_format_date_he(booking['date'])} "
                f"(משך הטיפול: {duration} דקות):\n{', '.join(free_slots)}\n\nאיזו שעה מתאימה לך?")

    if appointments.has_conflict(booking["date"], booking["time"], duration):   # השעה נתפסה בינתיים
        taken_time = booking["time"]
        booking["time"] = None
        state["stage"] = STAGE_BOOKING
        return f"מצטערים, השעה {taken_time} כבר תפוסה. אפשר לבחור שעה אחרת?"

    if not booking["gender"]:                                        # (4) מגדר בעל/ת התור (שדה חובה במערכת)
        state["stage"] = STAGE_BOOKING
        return "עבור מי התור - גבר או אישה?"

    if not state.get("identity_verified"):                           # (5) זיהוי - רק אחרי שהפרטים מלאים
        state["stage"] = STAGE_BOOKING_IDENTITY
        return ("מעולה, נשאר רק לזהות אותך כדי להשלים את ההזמנה. "
                "אפשר לכתוב את השם המלא, מספר הטלפון ותעודת הזהות שלך?")

    return _present_booking_summary(state)                           # (6) סיכום לאישור מפורש


def _validate_booking_date(date_text):
    """בודק שהתאריך שנבחר תקין לקביעת תור: לא בעבר, ושהקליניקה פתוחה בו. מחזיר הודעת שגיאה
    ידידותית אם יש בעיה, או None אם התאריך תקין."""
    try:
        chosen = datetime.date.fromisoformat(date_text)
    except ValueError:
        return "לא הצלחתי להבין את התאריך. אפשר לכתוב אותו בפורמט 15.03.2027?"
    if chosen < datetime.date.today():
        return "התאריך שציינת כבר עבר. אפשר לבחור תאריך עתידי?"
    if chosen.weekday() not in config.OPEN_WEEKDAYS:
        open_days = ", ".join(_WEEKDAY_NAMES_HE[d] for d in config.OPEN_WEEKDAYS)
        return (f"הקליניקה סגורה ביום {_WEEKDAY_NAMES_HE[chosen.weekday()]}. "
                f"אנחנו פתוחים בימים {open_days}. אפשר לבחור תאריך אחר?")
    return None


def _present_booking_summary(state):
    """מציג סיכום מלא של ההזמנה ומבקש אישור מפורש. שום דבר לא נכתב לבסיס הנתונים לפני זה."""
    booking = state["booking"]
    duration, price = _booking_total(booking)
    state["stage"] = STAGE_BOOKING_CONFIRM
    return (f"לסיכום, התור שאקבע עבורך:\n"
            f"• טיפולים: {', '.join(booking['service_names'])}\n"
            f"• תאריך: {_format_date_he(booking['date'])} בשעה {booking['time']}\n"
            f"• משך: {duration} דקות\n"
            f"• מחיר: {int(price)} ש\"ח\n\n"
            f"לאשר את ההזמנה? (כן / לא)")


def _handle_booking(state, user_message):
    """שלב איסוף פרטי ההזמנה: כל הודעה עוברת חילוץ, מה שנמצא נקלט לטיוטה, וממשיכים לחסר הבא."""
    extracted = _extract(user_message)
    if extracted is None:
        return "לא הצלחתי להבין. אפשר לנסח מחדש?"

    if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:      # בקשת ניהול באמצע הזמנה - סירוב
        return _ADMIN_REFUSAL

    if extracted["confirm"] == "no" or _yes_no_intent(user_message) == "negative":
        state["booking"] = _empty_booking()                    # ביטול תהליך ההזמנה לבקשת המשתמש/ת
        state["stage"] = STAGE_VERIFIED if state.get("identity_verified") else STAGE_START
        return "ביטלתי את תהליך קביעת התור. אפשר לעזור במשהו אחר?"

    before = dict(state["booking"])                           # צילום מצב, כדי לזהות אם התקדמנו
    _absorb_booking_details(state, extracted)

    if state["booking"] == before:                            # לא נקלט שום פרט חדש מההודעה
        public = _public_answer(extracted["intent"])            # אולי זו שאלה ציבורית באמצע התהליך
        if public:
            return public + "\n\n" + _start_or_continue_booking(state)

    return _start_or_continue_booking(state)


def _handle_booking_identity(state, user_message):
    """אוסף שם/טלפון/תעודת זהות כדי להשלים את ההזמנה: אם התעודה כבר רשומה במערכת - זהו אימות
    של לקוח/ה קיים/ת. אם היא אינה רשומה - נרשום לקוח/ה חדש/ה עם הפרטים שנמסרו."""
    extracted = _extract(user_message)
    if extracted is None:
        return "לא הצלחתי להבין. אפשר לכתוב שוב את השם המלא, הטלפון ותעודת הזהות?"

    if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
        return _ADMIN_REFUSAL

    registration = state["registration"]                      # קליטת כל פרט שנמסר, בהדרגה
    if extracted["name"]:
        registration["full_name"] = extracted["name"]
    if extracted["phone"]:
        registration["phone"] = extracted["phone"]
    if extracted["id_number"]:
        registration["id_number"] = extracted["id_number"]

    if not registration["full_name"]:
        return "מה השם המלא שלך?"
    if not registration["phone"]:
        return "מה מספר הטלפון שלך?"
    if not registration["id_number"]:
        return "ומה מספר תעודת הזהות שלך? (לצורך אימות בלבד)"

    existing = customers.find_customer_by_id_number(registration["id_number"])
    if existing:                                              # התעודה כבר רשומה - זהו לקוח/ה קיים/ת
        if not _name_matches(registration["full_name"], existing["full_name"]):
            # הגנת עומק: תעודת זהות של אדם אחד בשם של אדם אחר - לא מאמתים
            state["registration"] = _empty_registration()
            if _spend_attempt(state):
                return _blocked_message()
            return ("הפרטים שנמסרו אינם מתיישבים זה עם זה. "
                    f"אפשר לבדוק את השם ואת מספר תעודת הזהות ולנסות שוב? "
                    f"(נותרו {state['attempts_left']} ניסיונות)")
        state["candidate_customer_id"] = existing["id"]
        state["identity_verified"] = True                       # אימות מוצלח של לקוח/ה קיים/ת
        return _present_booking_summary(state)

    phone_owner = customers.find_customer_by_phone(registration["phone"])
    if phone_owner:
        # הטלפון שייך ללקוח/ה קיים/ת, אבל תעודת הזהות שנמסרה אינה שלו/ה. לא נרשום לקוח/ה חדש/ה
        # על טלפון קיים, וגם לא נקשר לרשומה הקיימת בלי אימות - שני המסלולים היו פרצת אבטחה.
        state["registration"] = _empty_registration()
        if _spend_attempt(state):
            return _blocked_message()
        return ("מספר הטלפון הזה כבר רשום אצלנו, אבל תעודת הזהות שנמסרה אינה תואמת לו. "
                "אם זה הטלפון שלך, אפשר להזין את תעודת הזהות הרשומה אצלנו. "
                f"(נותרו {state['attempts_left']} ניסיונות)")

    # לקוח/ה חדש/ה לגמרי: יוצרים רשומה. אין כאן סיכון פרטיות - הפרטים הם של מי שמוסר/ת אותם,
    # ולא נחשף שום מידע קיים. זו בדיוק ההתנהגות של טופס ההזמנה באתר, שיוצר לקוח/ה אוטומטית.
    new_id = customers.add_customer(
        full_name=registration["full_name"],
        phone=registration["phone"],
        id_number=registration["id_number"],
    )
    state["candidate_customer_id"] = new_id
    state["identity_verified"] = True      # הפרטים נמסרו על ידי בעליהם - מכאן זו זהות מאומתת
    return ("נרשמת אצלנו בהצלחה! 🎉\n\n" + _present_booking_summary(state))


def _handle_booking_confirm(state, user_message):
    """שלב האישור המפורש: רק תשובה חיובית ברורה תגרום לכתיבת התור לבסיס הנתונים."""
    extracted_confirm = None
    if not _yes_no_intent(user_message):                      # אם לא זוהה כן/לא מקומית
        extracted = _extract(user_message)                       # שואלים את המודל
        if extracted:
            if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
                return _ADMIN_REFUSAL
            extracted_confirm = extracted["confirm"]

    answer = _yes_no_intent(user_message) or (
        "positive" if extracted_confirm == "yes" else "negative" if extracted_confirm == "no" else None)

    if answer == "positive":
        return _book_appointment(state)                       # הפעולה עצמה - רק כאן
    if answer == "negative":
        state["booking"] = _empty_booking()
        state["stage"] = STAGE_VERIFIED
        return "בסדר גמור, לא קבעתי כלום. אפשר לעזור במשהו אחר?"
    return "לא הבנתי - לאשר את ההזמנה? אפשר לענות 'כן' או 'לא'."


def _book_appointment(state):
    """כותבת את התור לבסיס הנתונים. מוגנת ב-_assert_verified, ונקראת אך ורק אחרי אישור מפורש."""
    _assert_verified(state)                                   # שומר הסף - פעולה רק אחרי אימות
    booking = state["booking"]
    customer = customers.get_customer(state["candidate_customer_id"])

    try:
        appointments.add_appointment(
            customer_id=state["candidate_customer_id"],
            guest_name=customer["full_name"] if customer else None,
            guest_phone=customer["phone"] if customer else None,
            gender=booking["gender"],
            service_ids=booking["service_ids"],
            appointment_date=booking["date"],
            appointment_time=booking["time"],
        )
    except ValueError as error:                               # התנגשות או ולידציה שנכשלה
        state["booking"]["time"] = None
        state["stage"] = STAGE_BOOKING
        return f"לא הצלחתי לקבוע את התור: {error}\nאפשר לבחור שעה אחרת?"

    summary = (f"{', '.join(booking['service_names'])} ב-{_format_date_he(booking['date'])} "
               f"בשעה {booking['time']}")
    state["booking"] = _empty_booking()                       # ניקוי הטיוטה אחרי ביצוע מוצלח
    state["stage"] = STAGE_VERIFIED
    return f"התור נקבע בהצלחה! ✅\n{summary}\n\nנתראה! אפשר לבטל אותו כאן בצ'אט בכל שלב."


# ============================== ביטול תור ==============================

def _load_verified_appointment_list(state):
    """מחזירה את כל התורים העתידיים של הלקוח/ה המאומת/ת. כמו _load_verified_appointment, גם היא
    מתחילה בשומר הסף - אין גישה לרשימת התורים בלי אימות."""
    _assert_verified(state)
    return appointments.list_upcoming_appointments(state["candidate_customer_id"])


def _offer_cancellation(state, preferred_date=None):
    """פותח זרימת ביטול. אם ללקוח/ה יש יותר מתור אחד - *לא מנחשים* איזה לבטל אלא שואלים במפורש,
    כדי שלא נמחק בטעות את התור הלא נכון (מחיקה היא פעולה בלתי הפיכה)."""
    upcoming = _load_verified_appointment_list(state)         # שליפה מאובטחת - רק של מי שאומת/ה
    if not upcoming:
        state["stage"] = STAGE_VERIFIED
        return "לא נמצא לך תור עתידי פתוח לביטול."

    if preferred_date:                                        # אם צוין תאריך מסוים - מצמצמים לפיו
        matching = [a for a in upcoming if a["appointment_date"] == preferred_date]
        if len(matching) == 1:
            upcoming = matching

    if len(upcoming) == 1:                                    # תור יחיד - ישר לאישור
        state["cancel_target_id"] = upcoming[0]["id"]
        state["stage"] = STAGE_CANCEL_CONFIRM
        return f"התור שלך הוא ב-{_format_appointment_details(upcoming[0])}.\nלבטל אותו? (כן / לא)"

    lines = [f"{index}. {_format_appointment_details(appt)}"     # רשימה ממוספרת לבחירה
             for index, appt in enumerate(upcoming, start=1)]
    state["cancel_target_id"] = None
    state["stage"] = STAGE_CANCEL_CHOOSE
    return ("יש לך כמה תורים עתידיים:\n" + "\n".join(lines) +
            "\n\nאיזה מהם לבטל? אפשר לכתוב את המספר או את התאריך.")


def _handle_cancel_choose(state, user_message):
    """הלקוח/ה בוחר/ת איזה תור לבטל, מתוך רשימה ממוספרת - לפי מספר סידורי או לפי תאריך."""
    upcoming = _load_verified_appointment_list(state)
    if not upcoming:
        state["stage"] = STAGE_VERIFIED
        return "לא נמצא לך תור עתידי פתוח לביטול."

    stripped = user_message.strip()
    if stripped.isdigit() and 1 <= int(stripped) <= len(upcoming):   # נבחר מספר סידורי מהרשימה
        chosen = upcoming[int(stripped) - 1]
        state["cancel_target_id"] = chosen["id"]
        state["stage"] = STAGE_CANCEL_CONFIRM
        return f"לבטל את התור ב-{_format_appointment_details(chosen)}? (כן / לא)"

    extracted = _extract(user_message)                        # אחרת - אולי נכתב תאריך או בקשה אחרת
    if extracted:
        if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
            return _ADMIN_REFUSAL
        if extracted["confirm"] == "no":
            state["stage"] = STAGE_VERIFIED
            return "בסדר, לא ביטלתי כלום. אפשר לעזור במשהו אחר?"
        wanted_date = extracted["claimed_date"] or extracted["requested_date"]
        if wanted_date:
            matching = [a for a in upcoming if a["appointment_date"] == wanted_date]
            if len(matching) == 1:
                state["cancel_target_id"] = matching[0]["id"]
                state["stage"] = STAGE_CANCEL_CONFIRM
                return f"לבטל את התור ב-{_format_appointment_details(matching[0])}? (כן / לא)"

    lines = [f"{index}. {_format_appointment_details(appt)}"
             for index, appt in enumerate(upcoming, start=1)]
    return "לא הבנתי איזה תור לבטל. אפשר לכתוב את המספר שלו מהרשימה:\n" + "\n".join(lines)


def _handle_cancel_confirm(state, user_message):
    """שלב האישור לביטול: רק תשובה חיובית ברורה תמחק את התור בפועל."""
    answer = _yes_no_intent(user_message)
    if answer is None:
        extracted = _extract(user_message)
        if extracted:
            if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
                return _ADMIN_REFUSAL
            answer = ("positive" if extracted["confirm"] == "yes"
                      else "negative" if extracted["confirm"] == "no" else None)

    if answer == "positive":
        return _cancel_appointment(state)                     # הפעולה עצמה - רק כאן
    if answer == "negative":
        state["stage"] = STAGE_VERIFIED
        return "בסדר, השארתי את התור שלך כמו שהוא. אפשר לעזור במשהו אחר?"
    return "לא הבנתי - לבטל את התור? אפשר לענות 'כן' או 'לא'."


def _cancel_appointment(state):
    """מוחקת את התור מבסיס הנתונים. נקראת אך ורק אחרי אישור מפורש, ומוחקת אך ורק את התור
    שנבחר במפורש (cancel_target_id) מתוך התורים של הלקוח/ה שאומת/ה בשיחה הזו.

    שימו לב לבדיקה הכפולה למטה: לא די בכך שהמזהה שמור ב-state - אנחנו מוודאים מחדש שהתור הזה
    באמת שייך ללקוח/ה המאומת/ת, מול הרשימה הטרייה מבסיס הנתונים. כך גם אם משהו ישתבש ב-state,
    אי אפשר יהיה למחוק תור של אדם אחר."""
    upcoming = _load_verified_appointment_list(state)         # שומר הסף + התורים של המאומת/ת בלבד
    target_id = state.get("cancel_target_id")
    appt = next((a for a in upcoming if a["id"] == target_id), None)   # הבדיקה הכפולה

    if not appt:
        state["stage"] = STAGE_VERIFIED
        state["cancel_target_id"] = None
        return "לא הצלחתי לאתר את התור לביטול. אפשר לנסות שוב?"

    details = _format_appointment_details(appt)
    appointments.delete_appointment(appt["id"])                 # המחיקה עצמה
    state["cancel_target_id"] = None
    state["stage"] = STAGE_VERIFIED
    return f"התור בוטל בהצלחה. ✅\n(התור שבוטל: {details})\n\n{_BOOKING_HINT}"


# ============================== שיחה אחרי אימות ==============================

def _handle_verified(state, user_message):
    """שיחה אחרי אימות מוצלח. כל השליפות והפעולות כאן משתמשות אך ורק ב-candidate_customer_id
    השמור ב-state - כלומר בלקוח/ה שאומת/ה - ולעולם לא בשם כלשהו שיופיע בהודעה חדשה."""
    customer = customers.get_customer(state["candidate_customer_id"])
    if not customer:
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        state["identity_verified"] = False
        return "משהו השתבש. אפשר להתחיל שוב ולכתוב את השם שלך?"

    extracted = _extract(user_message)
    if extracted is None:
        return "לא הצלחתי להבין את השאלה. אפשר לנסח מחדש?"

    intent = extracted["intent"]

    if intent == nlu.INTENT_ADMIN_REQUEST:                    # פעולת ניהול - סירוב, גם אחרי אימות
        return _ADMIN_REFUSAL

    if extracted["name"] and not _name_matches(extracted["name"], customer["full_name"]):
        # שאלה על אדם אחר - סירוב מנומס, בלי שום שליפה של מידע עליו
        return ("אני יכול/ה למסור מידע ולבצע פעולות רק עבור מי שאומת/ה בשיחה הזו. "
                "כדי לטפל בתור של אדם אחר, יש להתחיל שיחה חדשה ולעבור אימות בנפרד.")

    if intent == nlu.INTENT_CANCEL:                           # בקשה מפורשת לבטל
        wanted = extracted["claimed_date"] or extracted["requested_date"]   # אולי צוין איזה תור
        return _offer_cancellation(state, preferred_date=wanted)

    if intent == nlu.INTENT_BOOK_NEW:                         # בקשה מפורשת לקבוע תור חדש
        _absorb_booking_details(state, extracted)
        return _start_or_continue_booking(state)

    public = _public_answer(intent)                           # שאלות ציבוריות נענות כרגיל
    if public:
        return public

    if extracted["claimed_date"]:                             # הלקוח/ה מציע/ה תאריך אחר לבדיקה
        state["claimed_date"] = extracted["claimed_date"]
        return _build_verified_reply(state, customer)

    if intent == nlu.INTENT_CHECK_APPOINTMENT:                # בקשה לראות שוב את פרטי התור
        return _build_verified_reply(state, customer)

    return ("אפשר לבקש ממני לבדוק את התור שלך, לקבוע תור חדש, לבטל תור קיים, "
            "או לשאול על מחירי טיפולים ושעות פתיחה.")
