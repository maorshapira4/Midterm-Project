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
STAGE_AWAIT_EMAIL = "await_email"         # מחכים להזנת האימייל לצורך אימות
STAGE_VERIFIED = "verified"               # הזהות אומתה בהצלחה בשיחה הזו
STAGE_BLOCKED = "blocked"                 # נחסם אחרי יותר מדי ניסיונות אימות כושלים
STAGE_BOOKING = "booking"                 # אוספים את פרטי התור החדש (טיפולים/תאריך/שעה/מגדר)
STAGE_BOOKING_IDENTITY = "booking_identity"   # אוספים פרטי זיהוי כדי להשלים את ההזמנה
STAGE_BOOKING_CONFIRM = "booking_confirm"       # מציגים סיכום ומחכים לאישור מפורש לפני שקובעים
STAGE_CANCEL_CHOOSE = "cancel_choose"             # ללקוח/ה יש כמה תורים - שואלים איזה מהם לבטל
STAGE_CANCEL_CONFIRM = "cancel_confirm"             # מציגים את התור ומחכים לאישור מפורש לפני ביטול

_POSITIVE_WORDS = {"כן", "נכון", "אכן", "בדיוק", "כמובן", "מאשר", "מאשרת", "yes", "yep", "ok", "אוקיי"}
_NEGATIVE_WORDS = {"לא", "שלילי", "טעות", "בטל", "no", "nope"}

_BOOKING_HINT = "אפשר לקבוע תור ממש כאן איתי - רק לכתוב 'אני רוצה לקבוע תור'."

_ADMIN_REFUSAL = (
    "אני כאן בשביל הלקוחות, ואין לי גישה לדברים של ההנהלה - רשימות לקוחות, תורים של אנשים "
    "אחרים, חשבוניות או נתוני הכנסות. מה שכן אני יכול/ה לעשות: לבדוק מתי התור שלך, "
    "לקבוע לך תור חדש, או לבטל תור קיים."
)

_PHRASING_SYSTEM_INSTRUCTION = (
    "את/ה העוזר/ת של קליניקת קוסמטיקה קטנה, ומדבר/ת עברית יומיומית, חמה וטבעית - כמו בן אדם "
    "שעונה ללקוחה בוואטסאפ, לא כמו מכתב רשמי. "
    "תקבל/י עובדות על תור של לקוח/ה, ואת התאריך שהוא/היא חשב/ה בטעות שהתור נמצא בו. "
    "נסח/י משפט אחד או שניים קצרים שמעדכנים מה התאריך והשעה האמיתיים ואילו טיפולים כלולים, "
    "ומעירים בעדינות שהתאריך שנאמר לא מדויק. "
    "כללים קשיחים: מותר להשתמש אך ורק בעובדות שסופקו לך בהודעה הזו. אסור להמציא, לשנות או "
    "להשלים שום נתון, ואסור להשתמש בשום ידע חיצוני או מידע מהאינטרנט - אין לך שום מקור מלבד "
    "מה שכתוב כאן. בלי הקדמות ארוכות ובלי נוסחאות נימוס מיותרות - ישר לעניין, בגובה העיניים."
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
    return {"service_ids": [], "service_names": [], "date": None, "time": None,
            "time_options": [], "gender": None}


def _empty_registration():
    """פרטי רישום ריקים: מה שנאסף עד כה עבור לקוח/ה שעדיין לא רשום/ה במערכת."""
    return {"full_name": None, "phone": None, "email": None}


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
        return state, "לא קיבלתי כלום... אפשר לכתוב שוב?"

    _ensure_state_shape(state)                                          # תאימות לשיחות שנפתחו בגרסה קודמת
    state["history"].append({"role": "user", "text": user_message})       # שמירת הודעת המשתמש בתמלול

    stage = state["stage"]                                               # השלב הנוכחי של השיחה
    if stage == STAGE_BLOCKED:
        reply = ("סגרתי את השיחה הזו אחרי יותר מדי ניסיונות שלא הצליחו - זה בשביל להגן על "
                 "הפרטים של הלקוחות שלנו. אפשר להתקשר אלינו, או ללחוץ על 'שיחה חדשה' ולנסות שוב.")
    elif stage == STAGE_START:
        reply = _handle_start(state, user_message)
    elif stage == STAGE_AWAIT_CONFIRM:
        reply = _handle_await_confirm(state, user_message)
    elif stage == STAGE_AWAIT_CLARIFY:
        reply = _handle_await_clarify(state, user_message)
    elif stage == STAGE_AWAIT_EMAIL:
        reply = _handle_await_email(state, user_message)
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
        reply = "אופס, משהו השתבש לי. אפשר ללחוץ על 'שיחה חדשה' ולנסות שוב?"

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


def _looks_like_email(text):
    """האם ההודעה מכילה בכלל משהו שנראה כמו כתובת אימייל. חשוב להוגנות: שאלה תמימה כמו
    'למה אתם צריכים את זה?' לא תיחשב ניסיון אימות כושל ולא תבזבז ניסיון יקר."""
    return bool(re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", text or ""))


def _extract_email(text):
    """שולף כתובת אימייל מתוך טקסט חופשי ומנרמל אותה, או None אם אין שם כזו."""
    match = re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", text or "")
    return match.group(0).strip().strip(".,;").lower() if match else None


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


def _extract(user_message, expecting=None):
    """עוטף את קריאת ה-NLU, ומזריק לה את רשימת הטיפולים האמיתית של הקליניקה ואת ההקשר
    (על מה בדיוק שאלנו כרגע), כדי שהמודל יפרש נכון קלט דו-משמעי כמו '16'."""
    return nlu.extract(user_message, available_service_names=_service_names_list(), expecting=expecting)


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
    return "אלה הטיפולים שלנו:\n" + "\n".join(lines)


def _hours_answer():
    """שעות וימי הפעילות של הקליניקה - מידע פומבי מקובץ ההגדרות."""
    open_days = ", ".join(_WEEKDAY_NAMES_HE[day] for day in config.OPEN_WEEKDAYS)
    return f"אנחנו פתוחים בימים {open_days}, בין {config.OPENING_TIME} ל-{config.CLOSING_TIME}."


def _public_answer(intent):
    """מחזיר תשובה לשאלה 'ציבורית', או None אם הכוונה אינה כזו."""
    if intent == nlu.INTENT_SERVICE_INFO:
        return _services_answer()
    if intent == nlu.INTENT_OPENING_HOURS:
        return _hours_answer()
    if intent == nlu.INTENT_GREETING:
        return ("היי! אני העוזר/ת של הקליניקה 💅 אפשר לשאול אותי מתי התור שלך, לקבוע תור חדש "
                "או לבטל אחד קיים (רק אוודא קודם שזה באמת את/ה), וגם על מחירים ושעות פתיחה.")
    if intent == nlu.INTENT_ADMIN_REQUEST:
        return _ADMIN_REFUSAL
    return None


# ============================== שלב 1: זיהוי ראשוני ==============================

def _handle_start(state, user_message):
    """שלב הזיהוי הראשוני: חילוץ הפרטים מההודעה, וניתוב לזרימה המתאימה."""
    # מסלול מהיר: הודעה שכולה כתובת אימייל ותו לא - אין כאן שפה להבין, ולכן מדלגים לחלוטין
    # על הקריאה ל-Gemini. התשובה מיידית, ונחסכת קריאת API מהמכסה החינמית.
    stripped = user_message.strip()
    if _looks_like_email(stripped) and " " not in stripped:
        return _verify_with_email(state, stripped)

    extracted = _extract(user_message)
    if extracted is None:
        return ("לא הצלחתי להבין, סליחה. אפשר לנסח את זה אחרת? "
                "למשל: 'קוראים לי דנה ויש לי תור ב-15.03.2027'.")

    return _route_extracted(state, extracted)


def _route_extracted(state, extracted):
    """מנתב לפי מה שחולץ מההודעה - משותף לשלב ההתחלה ולשלבים אחרים שנופלים חזרה לזיהוי."""
    intent = extracted["intent"]
    if extracted["claimed_date"]:
        state["claimed_date"] = extracted["claimed_date"]     # תאריך של תור קיים שהלקוח/ה טוען/ת

    _absorb_booking_details(state, extracted)                   # אם צוינו פרטי תור חדש - נשמור אותם

    if intent == nlu.INTENT_ADMIN_REQUEST:                      # בקשת פעולת ניהול - סירוב תמידי
        return _ADMIN_REFUSAL

    if intent in (nlu.INTENT_BOOK_NEW, nlu.INTENT_CHANGE_DETAILS):   # בקשה לקבוע תור, או לשנות פרט
        return _start_or_continue_booking(state)

    if intent == nlu.INTENT_CANCEL:                             # בקשה מפורשת לבטל תור
        if state["stage"] == STAGE_VERIFIED:
            return _offer_cancellation(state)
        return "בשמחה! רק שנייה - קודם אני צריך/ה לדעת מי את/ה. איך קוראים לך?"

    name, email = extracted["name"], extracted["email"]
    if not name and not email:                                  # לא נמסר שום פרט מזהה
        public = _public_answer(intent)
        if public:
            return public
        if extracted["confirm"] == "no":                          # "לא תודה" - לא חוזרים לשאול לשם
            return "בסדר גמור! אם תצטרך/י משהו - אני כאן. 😊"
        return ("כדי שאוכל לעזור, קודם כל - איך קוראים לך? "
                "ואם בא לך פשוט לקבוע תור, אפשר להגיד לי 'אני רוצה לקבוע תור'.")

    if email:                                                   # מסלול מהיר: נמסר אימייל
        return _verify_with_email(state, email, claimed_name=name)

    matches = customers.search_customers_by_name(name)          # חיפוש לקוחות תואמים לפי השם
    if len(matches) == 0:
        return (f"לא מצאתי אף אחד בשם '{name}' אצלנו. אולי כדאי לבדוק את האיות? "
                f"ואם זו הפעם הראשונה שלך - אין צורך להירשם מראש: פשוט תגיד/י לי "
                f"'אני רוצה לקבוע תור', ואפתח לך חשבון תוך כדי.")
    if len(matches) == 1:
        return _propose_candidate(state, matches[0])

    state["stage"] = STAGE_AWAIT_CLARIFY                        # 2+ תוצאות - לא מנחשים, מבקשים הבהרה
    return f"יש לנו כמה בשם '{name}'... מה השם המלא שלך?"


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
        return "עדיין יש כמה אפשרויות. אפשר שם פרטי ושם משפחה?"

    extracted = _extract(user_message)                           # 0 התאמות - אולי זו בכלל שאלה
    if extracted:
        public = _public_answer(extracted["intent"])
        if public:
            return public + "\n\nובחזרה לזיהוי: אפשר לכתוב את השם המלא שלך?"
        if extracted["email"]:
            return _verify_with_email(state, extracted["email"], claimed_name=extracted["name"])
        if extracted["name"]:
            by_name = customers.search_customers_by_name(extracted["name"])
            if len(by_name) == 1:
                return _propose_candidate(state, by_name[0])
    return "עדיין לא מצאתי. אפשר לכתוב את השם המלא בדיוק כמו שהוא רשום אצלנו?"


def _handle_await_confirm(state, user_message):
    """המשתמש/ת מאשר/ת או מכחיש/ה שזה השם המלא שלו/ה, לפני שמבקשים תעודת זהות."""
    intent = _yes_no_intent(user_message)
    if intent == "positive":
        state["stage"] = STAGE_AWAIT_EMAIL
        return "מעולה. רק כדי לוודא שזה באמת את/ה - מה האימייל שרשום אצלנו?"
    if intent == "negative":
        state["candidate_customer_id"] = None
        state["identity_verified"] = False     # ביטול המועמד/ת מכבה גם את דגל האימות
        state["stage"] = STAGE_START
        return "אוי, סליחה! אז מה השם המלא שלך?"

    if _looks_like_email(user_message):        # המשתמש/ת "קפץ/ה קדימה" והזין/ה ת.ז.
        state["stage"] = STAGE_AWAIT_EMAIL
        return _handle_await_email(state, user_message)

    extracted = _extract(user_message)
    if extracted:
        public = _public_answer(extracted["intent"])
        if public:
            return public + "\n\nובחזרה לזיהוי: השם שהצגתי הוא הנכון? (כן / לא)"
        if extracted["name"]:
            matches = customers.search_customers_by_name(extracted["name"])
            if len(matches) == 1:
                return _propose_candidate(state, matches[0])
    return "לא הבנתי - זה השם הנכון? אפשר לענות כן או לא."


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
    return ("היו כאן יותר מדי ניסיונות שלא הצליחו, אז אני סוגר/ת את השיחה - זה בשביל להגן "
            "על הפרטים של הלקוחות שלנו. אפשר להתקשר אלינו, או להתחיל שיחה חדשה.")


def _after_verification(state, customer):
    """נקרא מיד אחרי אימות מוצלח: אם הייתה בקשה שממתינה לאימות (קביעת תור/ביטול) - ממשיכים
    אליה. אחרת מציגים את פרטי התור הקיים, שזו ברירת המחדל."""
    state["stage"] = STAGE_VERIFIED
    state["identity_verified"] = True      # נקודת האמת: מכאן ואילך מותר מידע אישי ופעולות
    booking = state["booking"]
    if booking["service_ids"] or booking["date"] or booking["time"]:   # הייתה בקשה לקבוע תור
        return _start_or_continue_booking(state)
    return _build_verified_reply(state, customer)


def _verify_with_email(state, email, claimed_name=None):
    """אימות לפי כתובת האימייל הרשומה במערכת. רק מי שיודע/ת את האימייל שרשום אצלנו עבור אותו/ה
    לקוח/ה יכול/ה להזדהות. כל ניסיון כושל צורך ניסיון מהמכסה, כדי שלא ניתן יהיה לנחש כתובות."""
    customer = customers.find_customer_by_email(email)
    if customer is None:                                    # האימייל לא רשום אצלנו
        if _spend_attempt(state):
            return _blocked_message()
        return ("האימייל הזה לא רשום אצלנו. אם זו הפעם הראשונה שלך - איזה כיף, נשמח לארח אותך! "
                f"{_BOOKING_HINT} ואם כבר היית אצלנו, שווה לבדוק אם זו הכתובת הנכונה.")

    if claimed_name and not _name_matches(claimed_name, customer["full_name"]):
        # הגנת עומק: אימייל אמיתי, אבל בשם שלא מסתדר עם מה שרשום עליו אצלנו
        if _spend_attempt(state):
            return _blocked_message()
        return "רגע, השם והאימייל לא ממש מסתדרים לי יחד. אפשר לבדוק אותם שוב?"

    state["candidate_customer_id"] = customer["id"]     # מרגע זה זהו הלקוח/ה של השיחה
    return _after_verification(state, customer)


def _handle_await_email(state, user_message):
    """השוואת האימייל שהוזן מול זה שרשום אצלנו עבור המועמד/ת. השלב הקריטי מבחינת אבטחה."""
    candidate = customers.get_customer(state["candidate_customer_id"])
    if not candidate:                                     # הגנה (למשל אם הלקוח/ה נמחק/ה באמצע)
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        state["identity_verified"] = False
        return "משהו השתבש לי כאן. אפשר להתחיל מהתחלה - איך קוראים לך?"

    if not _looks_like_email(user_message):               # ההודעה אינה ניסיון להזין אימייל
        extracted = _extract(user_message)
        if extracted:
            public = _public_answer(extracted["intent"])     # שאלה לגיטימית - עונים בלי לגבות ניסיון
            if public:
                return public + "\n\nוכדי להמשיך, רק צריך את האימייל שרשום אצלנו."
        # חשוב: לא מנצלים כאן ניסיון - לא היה כאן ניחוש בכלל
        return ("אני רוצה לוודא שזה באמת את/ה לפני שאני מוסר/ת פרטים או עושה משהו בחשבון. "
                "מה האימייל שרשום אצלנו?")

    entered = _extract_email(user_message)                # האימייל שהוזן, מנורמל
    real = (candidate.get("email") or "").strip().lower()   # והאימייל הרשום במערכת

    if real and entered == real:                          # התאמה מדויקת - ורק זה מאפשר המשך
        return _after_verification(state, candidate)

    other_owner = customers.find_customer_by_email(entered)   # האם הכתובת בכלל קיימת אצלנו?
    if _spend_attempt(state):
        return _blocked_message()

    remaining = state["attempts_left"]
    if other_owner is None:
        return (f"האימייל הזה לא רשום אצלנו. אם זו הפעם הראשונה שלך - {_BOOKING_HINT} "
                f"ואם כבר היית אצלנו, אפשר לנסות כתובת אחרת (נשארו {remaining} ניסיונות).")
    # הכתובת קיימת אך שייכת ללקוח/ה אחר/ת - לא חושפים בשום אופן למי
    return (f"האימייל הזה לא מתאים למה שרשום אצלנו על {candidate['full_name']}. "
            f"נשארו {remaining} ניסיונות.")


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
        return (f"היי {candidate['full_name']}! אין לך תור פתוח כרגע. {_BOOKING_HINT}")

    if claimed_date and claimed_date != appt["appointment_date"]:      # פער בין הטענה למציאות
        phrased = _phrase_correction(candidate["full_name"], claimed_date, appt)
        if phrased:
            return phrased
        return (f"היי {candidate['full_name']}! שים/י לב - התור שלך הוא בעצם ב-"
                f"{_format_appointment_details(appt)}, ולא ב-{_format_date_he(claimed_date)} כמו שאמרת.")

    return f"היי {candidate['full_name']}! התור שלך הוא ב-{_format_appointment_details(appt)}."


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
        booking["time_options"] = []

    times = extracted.get("requested_times") or []
    if len(times) == 1:                    # שעה אחת ברורה - מקבלים אותה
        booking["time"] = times[0]
        booking["time_options"] = []
    elif len(times) > 1:
        # הלקוח/ה הזכיר/ה כמה שעות ("מתאים לי 16:30 או 13:00"). *לא* בוחרים עבורו/ה אחת
        # באופן שרירותי - זו בדיוק התקלה שנצפתה בשיחה אמיתית, שבה הבוט קבע לבד 13:00.
        # שומרים את האפשרויות ושואלים איזו מהן.
        booking["time"] = None
        booking["time_options"] = times

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
        return "יאללה, בואו נקבע! מה בא לך לעשות?\n" + _services_answer()

    if not booking["date"]:                                          # (2) באיזה תאריך
        state["stage"] = STAGE_BOOKING
        return (f"סבבה, {', '.join(booking['service_names'])}. לאיזה תאריך? "
                f"(אפשר גם 'מחר' או 'יום ראשון הקרוב')")

    date_problem = _validate_booking_date(booking["date"])           # ולידציה של התאריך שנבחר
    if date_problem:
        booking["date"] = None
        state["stage"] = STAGE_BOOKING
        return date_problem

    duration, _price = _booking_total(booking)

    if not booking["time"] and booking["time_options"]:              # (3א) הוזכרו כמה שעות - שואלים איזו
        free_slots = appointments.get_free_slots(booking["date"], duration)
        available = [t for t in booking["time_options"] if t in free_slots]   # רק אלה שבאמת פנויות
        state["stage"] = STAGE_BOOKING
        if len(available) == 1:                                        # רק אחת מהן פנויה - אין דו-משמעות
            booking["time"] = available[0]
            booking["time_options"] = []
            return (f"מבין השעות שציינת, רק {available[0]} פנויה - אז שמתי אותה. "
                    + _start_or_continue_booking(state))
        if not available:                                               # אף אחת לא פנויה
            booking["time_options"] = []
            return (f"אף אחת מהשעות שציינת לא פנויה ב-{_format_date_he(booking['date'])}. "
                    f"אלה כן פנויות:\n{', '.join(free_slots)}\n\nמה מתאים לך?")
        return f"איזו מהן עדיפה לך - {' או '.join(available)}?"          # יש כמה - שואלים

    if not booking["time"]:                                          # (3ב) באיזו שעה
        free_slots = appointments.get_free_slots(booking["date"], duration)
        state["stage"] = STAGE_BOOKING
        if not free_slots:
            booking["date"] = None
            return (f"מצטערים, אין משבצות פנויות ב-{_format_date_he(booking['date'])} "
                    f"עבור {duration} דקות טיפול. אפשר לנסות תאריך אחר?")
        return (f"אלה השעות הפנויות ב-{_format_date_he(booking['date'])} "
                f"(משך הטיפול: {duration} דקות):\n{', '.join(free_slots)}\n\nאיזו שעה מתאימה לך?")

    # השעה שנבחרה חייבת להיות אחת מהמשבצות הפנויות בפועל - לא מספיק שהיא "לא מתנגשת".
    # בלי הבדיקה הזו אפשר היה לבקש 14:15 ולקבל תור, למרות שהקליניקה עובדת רק בשעות עגולות
    # ובחצאי שעה. הרשימה כאן היא בדיוק אותה רשימה שהוצגה ללקוח/ה, ולכן היא גם מכבדת תורים
    # שנקבעו בינתיים - כולל כאלה שנקבעו דרך הצ'אט עצמו.
    free_slots = appointments.get_free_slots(booking["date"], duration)
    if booking["time"] not in free_slots:
        requested = booking["time"]
        booking["time"] = None
        state["stage"] = STAGE_BOOKING
        if not free_slots:
            booking["date"] = None
            return (f"אין לי משבצות פנויות ב-{_format_date_he(booking['date'] or '')} "
                    f"לטיפול של {duration} דקות. אפשר לנסות תאריך אחר?")
        on_grid = appointments.list_slot_times(duration)      # האם השעה בכלל קיימת ברשת?
        reason = ("כבר תפוסה" if requested in on_grid
                  else f"לא אחת מהשעות שאנחנו עובדים בהן (אנחנו בקפיצות של "
                       f"{config.SLOT_LENGTH_MINUTES} דקות)")
        return (f"השעה {requested} {reason}. אלה השעות שפנויות ב-"
                f"{_format_date_he(booking['date'])}:\n{', '.join(free_slots)}\n\nמה מתאים לך?")

    if not booking["gender"]:                                        # (4) מגדר בעל/ת התור (שדה חובה במערכת)
        state["stage"] = STAGE_BOOKING
        return "ועוד דבר קטן שאני צריך/ה לרישום - התור הוא לגבר, לאישה, או שתעדיף/י לא לציין?"

    if not state.get("identity_verified"):                           # (5) זיהוי - רק אחרי שהפרטים מלאים
        state["stage"] = STAGE_BOOKING_IDENTITY
        return ("מעולה! נשאר רק להכיר - מה השם המלא, הטלפון והאימייל שלך?")

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
            f"מאשר/ת? (כן / לא)")


def _booking_expecting(state):
    """מחזיר מחרוזת שמתארת מה בדיוק שאלנו כרגע בזרימת ההזמנה, כדי שה-NLU יפרש נכון קלט
    דו-משמעי. בלי זה, '16' בשלב בחירת השעה נקרא בטעות כתאריך ה-16 בחודש."""
    booking = state["booking"]
    if not booking["service_ids"]:
        return None
    if not booking["date"]:
        return "date"
    if not booking["time"]:
        return "time"
    return None


def _handle_booking(state, user_message):
    """שלב איסוף פרטי ההזמנה: כל הודעה עוברת חילוץ, מה שנמצא נקלט לטיוטה, וממשיכים לחסר הבא."""
    extracted = _extract(user_message, expecting=_booking_expecting(state))
    if extracted is None:
        return "לא הצלחתי להבין. אפשר לנסח מחדש?"

    if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:      # בקשת ניהול באמצע הזמנה - סירוב
        return _ADMIN_REFUSAL

    if extracted["confirm"] == "no" or _yes_no_intent(user_message) == "negative":
        state["booking"] = _empty_booking()                    # ביטול תהליך ההזמנה לבקשת המשתמש/ת
        state["stage"] = STAGE_VERIFIED if state.get("identity_verified") else STAGE_START
        return "אין בעיה, עזבתי את זה. משהו אחר?"

    before = dict(state["booking"])                           # צילום מצב, כדי לזהות אם התקדמנו
    _absorb_booking_details(state, extracted)

    if state["booking"] == before:                            # לא נקלט שום פרט חדש מההודעה
        public = _public_answer(extracted["intent"])            # אולי זו שאלה ציבורית באמצע התהליך
        if public:
            return public + "\n\n" + _start_or_continue_booking(state)

    return _start_or_continue_booking(state)


def _handle_booking_identity(state, user_message):
    """אוסף שם/טלפון/אימייל כדי להשלים את ההזמנה: אם האימייל כבר רשום אצלנו - זהו אימות של
    לקוח/ה קיים/ת. אם הוא לא רשום - נרשום לקוח/ה חדש/ה עם הפרטים שנמסרו."""
    extracted = _extract(user_message, expecting="identity")
    if extracted is None:
        return "לא הצלחתי להבין. אפשר לכתוב שוב את השם, הטלפון והאימייל?"

    if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
        return _ADMIN_REFUSAL

    registration = state["registration"]                      # קליטת כל פרט שנמסר, בהדרגה
    if extracted["name"]:
        registration["full_name"] = extracted["name"]
    if extracted["phone"]:
        registration["phone"] = extracted["phone"]
    if extracted["email"]:
        registration["email"] = extracted["email"]

    if not registration["full_name"]:
        return "איך קוראים לך?"
    if not registration["phone"]:
        return "ומה מספר הטלפון שלך?"
    if not registration["email"]:
        return "ואיזה אימייל? (הוא ישמש אותי לזהות אותך בפעם הבאה)"

    existing = customers.find_customer_by_email(registration["email"])
    if existing:                                              # האימייל כבר רשום - זהו לקוח/ה קיים/ת
        if not _name_matches(registration["full_name"], existing["full_name"]):
            # הגנת עומק: אימייל של אדם אחד בשם של אדם אחר - לא מאמתים
            state["registration"] = _empty_registration()
            if _spend_attempt(state):
                return _blocked_message()
            return ("רגע, השם והאימייל לא ממש מסתדרים לי יחד. אפשר לבדוק אותם שוב? "
                    f"(נשארו {state['attempts_left']} ניסיונות)")
        state["candidate_customer_id"] = existing["id"]
        state["identity_verified"] = True                       # אימות מוצלח של לקוח/ה קיים/ת
        return _present_booking_summary(state)

    phone_owner = customers.find_customer_by_phone(registration["phone"])
    if phone_owner:
        # הטלפון שייך ללקוח/ה קיים/ת, אבל האימייל שנמסר אינו שלו/ה. לא נרשום לקוח/ה חדש/ה על
        # טלפון קיים (זה היה יוצר רשומות כפולות על מספר של מישהו אחר), וגם לא נקשר לרשומה
        # הקיימת בלי אימות - שני המסלולים היו פרצה.
        state["registration"] = _empty_registration()
        if _spend_attempt(state):
            return _blocked_message()
        return ("הטלפון הזה כבר רשום אצלנו, אבל עם אימייל אחר. אם זה הטלפון שלך, אפשר לכתוב "
                f"את האימייל שרשום אצלנו. (נשארו {state['attempts_left']} ניסיונות)")

    # לקוח/ה חדש/ה לגמרי: יוצרים רשומה. אין כאן סיכון פרטיות - הפרטים הם של מי שמוסר/ת אותם,
    # ולא נחשף שום מידע קיים. זו בדיוק ההתנהגות של טופס ההזמנה באתר, שיוצר לקוח/ה אוטומטית.
    try:
        new_id = customers.add_customer(
            full_name=registration["full_name"],
            phone=registration["phone"],
            email=registration["email"],
        )
    except ValueError as error:                               # אימייל לא תקין או כפול
        state["registration"]["email"] = None                    # מנקים רק את האימייל, לא את הכל
        return f"{error}. אפשר לנסות כתובת אחרת?"

    state["candidate_customer_id"] = new_id
    state["identity_verified"] = True      # הפרטים נמסרו על ידי בעליהם - מכאן זו זהות מאומתת
    return ("נרשמת אצלנו, ברוך/ה הבא/ה! 🎉\n\n" + _present_booking_summary(state))


def _handle_booking_confirm(state, user_message):
    """שלב האישור המפורש. שלוש אפשרויות: אישור (וקביעה בפועל), ביטול, או *שינוי* פרט בהזמנה.
    האפשרות השלישית חשובה: בשיחה אמיתית לקוח כתב 'בעצם בא לי בשעה 16:00' בשלב הזה, והבוט
    ענה לו 'לא הבנתי - לקבוע את התור? כן או לא?' במקום פשוט לשנות. עכשיו אפשר לשנות."""
    local_answer = _yes_no_intent(user_message)      # בדיקה מקומית מהירה, בלי לבזבז קריאת API

    # "כן"/"לא" חד-משמעיים ובודדים - אין צורך בשום עיבוד נוסף
    if local_answer == "positive" and len(user_message.split()) <= 2:
        return _book_appointment(state)
    if local_answer == "negative" and len(user_message.split()) <= 2:
        state["booking"] = _empty_booking()
        state["stage"] = STAGE_VERIFIED
        return "בסדר גמור, לא קבעתי כלום. משהו אחר?"

    extracted = _extract(user_message, expecting="time")   # אחרת - מבינים לעומק מה נאמר
    if extracted is None:
        return "לא הבנתי - לקבוע את התור כמו שסיכמנו? כן או לא?"

    if extracted["intent"] == nlu.INTENT_ADMIN_REQUEST:
        return _ADMIN_REFUSAL

    # בקשת שינוי: קולטים את מה שהתבקש ומציגים סיכום מעודכן, במקום לדרוש כן/לא
    before = dict(state["booking"])
    _absorb_booking_details(state, extracted)
    if state["booking"] != before:                        # באמת השתנה משהו
        state["stage"] = STAGE_BOOKING
        return "אין בעיה, עדכנתי. " + _start_or_continue_booking(state)

    if extracted["intent"] == nlu.INTENT_CHANGE_DETAILS:
        # הלקוח/ה רוצה לשנות משהו אך לא אמר/ה מה בדיוק
        state["stage"] = STAGE_BOOKING
        state["booking"]["time"] = None
        state["booking"]["time_options"] = []
        return "בטח, מה תרצה/י לשנות? " + _start_or_continue_booking(state)

    if extracted["confirm"] == "yes" or local_answer == "positive":
        return _book_appointment(state)
    if extracted["confirm"] == "no" or local_answer == "negative":
        state["booking"] = _empty_booking()
        state["stage"] = STAGE_VERIFIED
        return "בסדר גמור, לא קבעתי כלום. משהו אחר?"

    return "לא הבנתי - לקבוע את התור כמו שסיכמנו? אפשר גם להגיד לי מה לשנות."


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
    return f"סגור! ✅\n{summary}\n\nנתראה! אם משהו משתנה - אפשר לבטל כאן בכל רגע."


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
        return "אין לך תור עתידי לבטל."

    if preferred_date:                                        # אם צוין תאריך מסוים - מצמצמים לפיו
        matching = [a for a in upcoming if a["appointment_date"] == preferred_date]
        if len(matching) == 1:
            upcoming = matching

    if len(upcoming) == 1:                                    # תור יחיד - ישר לאישור
        state["cancel_target_id"] = upcoming[0]["id"]
        state["stage"] = STAGE_CANCEL_CONFIRM
        return f"התור שלך הוא ב-{_format_appointment_details(upcoming[0])}.\nלבטל? (כן / לא)"

    lines = [f"{index}. {_format_appointment_details(appt)}"     # רשימה ממוספרת לבחירה
             for index, appt in enumerate(upcoming, start=1)]
    state["cancel_target_id"] = None
    state["stage"] = STAGE_CANCEL_CHOOSE
    return ("יש לך כמה תורים:\n" + "\n".join(lines) +
            "\n\nאיזה מהם לבטל? אפשר פשוט את המספר.")


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
        return "אוקיי, השארתי אותו. משהו אחר?"
    return "לא הבנתי - לבטל את התור? כן או לא?"


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
    return f"בוטל. ✅\n({details})\n\n{_BOOKING_HINT}"


# ============================== שיחה אחרי אימות ==============================

def _handle_verified(state, user_message):
    """שיחה אחרי אימות מוצלח. כל השליפות והפעולות כאן משתמשות אך ורק ב-candidate_customer_id
    השמור ב-state - כלומר בלקוח/ה שאומת/ה - ולעולם לא בשם כלשהו שיופיע בהודעה חדשה."""
    customer = customers.get_customer(state["candidate_customer_id"])
    if not customer:
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        state["identity_verified"] = False
        return "משהו השתבש לי. אפשר להתחיל מחדש - איך קוראים לך?"

    extracted = _extract(user_message)
    if extracted is None:
        return "לא בטוח/ה שהבנתי. אפשר לנסח אחרת?"

    intent = extracted["intent"]

    if intent == nlu.INTENT_ADMIN_REQUEST:                    # פעולת ניהול - סירוב, גם אחרי אימות
        return _ADMIN_REFUSAL

    if extracted["name"] and not _name_matches(extracted["name"], customer["full_name"]):
        # שאלה על אדם אחר - סירוב מנומס, בלי שום שליפה של מידע עליו
        return ("אני יכול/ה לעזור רק למי שהזדהה/תה כאן בשיחה. בשביל תור של מישהו אחר - "
                "צריך להתחיל שיחה חדשה ולהזדהות בנפרד.")

    if intent == nlu.INTENT_CANCEL:                           # בקשה מפורשת לבטל
        wanted = extracted["claimed_date"] or extracted["requested_date"]   # אולי צוין איזה תור
        return _offer_cancellation(state, preferred_date=wanted)

    if intent in (nlu.INTENT_BOOK_NEW, nlu.INTENT_CHANGE_DETAILS):   # קביעת תור חדש, או שינוי
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

    return ("אפשר לבקש ממני לבדוק את התור שלך, לקבוע חדש, לבטל קיים, "
            "או לשאול על מחירים ושעות פתיחה. אלה הדברים שאני מטפל/ת בהם.")
