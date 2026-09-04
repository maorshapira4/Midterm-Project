"""
chatbot/conversation.py - מוח הצ'אטבוט: מנהל את מצב השיחה (מי המועמד/ת הנוכחי/ת, האם הוא/היא
כבר אומת/ה, כמה ניסיונות אימות נותרו) ומממש שלב-אחר-שלב את הזרימה: זיהוי -> הבהרה (אם צריך)
-> אימות תעודת זהות -> תשובה מבוססת דאטה אמיתי.

כל הפונקציות כאן מקבלות ומחזירות את מצב השיחה (state) כ-dict רגיל של פייתון - אין כאן שום
תלות ב-Flask עצמו. routes/chatbot_routes.py הוא זה ששומר את ה-dict הזה בפועל בתוך session
של Flask, בין הודעה להודעה. ההפרדה הזו הופכת את לוגיקת השיחה לקלה יותר להבנה ולבדיקה.

------------------------------------------------------------------------------------------
דרישת האבטחה הקשיחה של הפרויקט, וכיצד היא נאכפת כאן בפועל:
לפני אימות תעודת זהות מוצלח, אסור לחשוף שום פרט אישי - לא תאריך, לא שעה, לא מחיר, כלום.
האכיפה כאן היא מבנית, לא "יוצא ככה במקרה":
  1. הקריאה היחידה בקובץ הזה למידע על תורים (appointments.get_next_upcoming_appointment)
     נמצאת בתוך _load_verified_appointment, שמתחילה בקריאה ל-_assert_verified.
  2. _assert_verified זורקת שגיאה אם השיחה אינה במצב STAGE_VERIFIED - כלומר גם באג עתידי
     שיקרא לפונקציה מוקדם מדי ייפול מיד, במקום לדלוף מידע בשקט.
  3. המידע האישי היחיד שנשלח אי פעם ל-Gemini לניסוח הוא של הלקוח/ה שכבר אומת/ה, ורק אחרי
     האימות. הודעת המשתמש/ת עצמה לעולם לא נשלחת לשלב הניסוח - כדי שלא ניתן יהיה להזריק
     דרכה הוראות למודל (prompt injection).
  4. שאלות "ציבוריות" (מחירי טיפולים, שעות פתיחה, איך קובעים תור) נענות בכל שלב - אבל הן
     מגיעות מטבלת השירותים ומקובץ ההגדרות, שהם ממילא מידע פומבי המוצג באתר לכל אחד, ואינם
     קשורים לאף לקוח/ה מסוים/ת.
------------------------------------------------------------------------------------------
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import re   # לזיהוי מילות כן/לא ולחילוץ ספרות מתוך הודעות המשתמש

import config                          # קובץ ההגדרות (מספר ניסיונות האימות המותר, שעות פעילות)
import models.customers as customers     # חיפוש לקוח לפי שם/ת.ז., שליפת פרטי לקוח
import models.appointments as appointments  # שליפת התור העתידי האמיתי - רק אחרי אימות!
import chatbot.nlu as nlu                     # חילוץ כוונה/שם/תאריך/ת.ז. מהודעה חופשית (Gemini)
import chatbot.gemini_client as gemini_client   # ניסוח התשובה הסופית בשפה טבעית (Gemini)

# שלבי השיחה האפשריים - "מכונת מצבים" (state machine) פשוטה
STAGE_START = "start"                     # עדיין לא ידוע מי המשתמש/ת
STAGE_AWAIT_CONFIRM = "await_confirm"     # נמצא לקוח יחיד תואם, מחכים לאישור "כן/לא זה אני"
STAGE_AWAIT_CLARIFY = "await_clarify"     # נמצאו כמה לקוחות תואמים, מחכים לשם מלא לצורך הבהרה
STAGE_AWAIT_ID = "await_id"               # מחכים להזנת תעודת זהות לצורך אימות
STAGE_VERIFIED = "verified"               # הזהות אומתה בהצלחה בשיחה הזו
STAGE_BLOCKED = "blocked"                 # נחסם אחרי יותר מדי ניסיונות אימות כושלים

_POSITIVE_WORDS = {"כן", "נכון", "אכן", "בדיוק", "כמובן", "yes", "yep", "ok", "אוקיי"}   # תשובה חיובית
_NEGATIVE_WORDS = {"לא", "שלילי", "טעות", "no", "nope"}                                    # תשובה שלילית

_BOOKING_HINT = "כדי לקבוע תור חדש אפשר ללחוץ על 'קביעת תור' בתפריט למעלה."   # הפניה קבועה למסך ההזמנה

_PHRASING_SYSTEM_INSTRUCTION = (
    "את/ה עוזר/ת קליניקת קוסמטיקה ידידותי/ת, שמנוסח/ת בעברית תקנית. תקבל/י עובדות אמיתיות על "
    "תור של לקוח/ה, ואת התאריך שהלקוח/ה חשב/ה (בטעות) שהתור נמצא בו. נסח/י משפט אחד או שניים "
    "קצרים, אדיבים וברורים בעברית, שמודיעים ללקוח/ה מה התאריך והשעה האמיתיים של התור (ואילו "
    "שירותים כלולים בו), ומציינים בעדינות שהתאריך שהוא/היא ציין/ה שגוי. "
    "חשוב מאוד: אסור לך להמציא, לשנות או לנחש שום נתון - יש להשתמש אך ורק בדיוק בעובדות שסופקו "
    "לך בהודעה. אל תוסיף/י ברכות מיותרות או הקדמות ארוכות - ישר לעניין."
)

# שמות הימים בעברית, לפי weekday() של פייתון: שני=0, שלישי=1 ... ראשון=6
_WEEKDAY_NAMES_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


# ============================== ניהול מצב השיחה ==============================

def new_state():
    """יוצר מצב שיחה חדש וריק - נקודת ההתחלה, לפני שידוע מיהו הלקוח/ה. גם משמש כדי 'לאתחל'
    שיחה קיימת (למשל בלחיצה על 'שיחה חדשה')."""
    return {
        "stage": STAGE_START,                                          # מתחילים תמיד משלב הזיהוי הראשוני
        "candidate_customer_id": None,                                     # עדיין אין מועמד/ת
        "claimed_date": None,                                                # עדיין אין תאריך נטען
        "attempts_left": config.CHATBOT_MAX_VERIFICATION_ATTEMPTS,             # מספר ניסיונות האימות המלא
        "history": [],                                                          # תמלול השיחה, להצגה במסך
    }


def _assert_verified(state):
    """שומר הסף של דרישת האבטחה: מוודא שהשיחה באמת עברה אימות תעודת זהות מוצלח לפני שניגשים
    למידע אישי כלשהו. אם לא - זורק שגיאה במקום להחזיר מידע. זו הגנה מבנית מפני באג עתידי:
    עדיף שהמערכת תיפול בקול רם מאשר תדלוף פרטי לקוח/ה בשקט."""
    if state.get("stage") != STAGE_VERIFIED or not state.get("candidate_customer_id"):
        raise PermissionError("ניסיון לגשת למידע אישי לפני אימות תעודת זהות - הפעולה נחסמה")


def handle_message(state, user_message):
    """נקודת הכניסה המרכזית: מקבלת את מצב השיחה הנוכחי ואת ההודעה החדשה של המשתמש/ת, ומחזירה
    (state מעודכן, תשובת הבוט כטקסט). הפונקציה משנה את ה-state שהתקבל ישירות (dict הוא mutable)
    ומחזירה גם אותו, לנוחות הקריאה מה-route."""
    user_message = (user_message or "").strip()                        # ניקוי רווחים מיותרים בהתחלה/בסוף
    if not user_message:                                                  # הודעה ריקה - אין מה לעבד
        return state, "לא קיבלתי שום הודעה. אפשר לכתוב שוב?"

    state["history"].append({"role": "user", "text": user_message})     # שמירת הודעת המשתמש בתמלול השיחה

    stage = state["stage"]                                               # השלב הנוכחי של השיחה
    if stage == STAGE_BLOCKED:                                             # אם השיחה כבר נחסמה
        reply = ("השיחה הזו נחסמה בעקבות יותר מדי ניסיונות אימות שגויים, מטעמי אבטחה. "
                  "אפשר לפנות אלינו טלפונית, או ללחוץ על 'שיחה חדשה' כדי להתחיל מחדש.")
    elif stage == STAGE_START:                                            # שלב הזיהוי הראשוני
        reply = _handle_start(state, user_message)
    elif stage == STAGE_AWAIT_CONFIRM:                                    # ממתינים לאישור "כן/לא, זה אני"
        reply = _handle_await_confirm(state, user_message)
    elif stage == STAGE_AWAIT_CLARIFY:                                    # ממתינים לשם מלא, בגלל כמה התאמות
        reply = _handle_await_clarify(state, user_message)
    elif stage == STAGE_AWAIT_ID:                                         # ממתינים לתעודת זהות לאימות
        reply = _handle_await_id(state, user_message)
    elif stage == STAGE_VERIFIED:                                         # כבר אומת/ה בשיחה הזו
        reply = _handle_verified(state, user_message)
    else:                                                                  # מצב לא צפוי (הגנה תיאורטית בלבד)
        reply = "משהו השתבש. אפשר ללחוץ על 'שיחה חדשה' ולנסות שוב?"

    state["history"].append({"role": "bot", "text": reply})             # שמירת תשובת הבוט בתמלול השיחה
    return state, reply                                                    # החזרת המצב המעודכן והתשובה לקריאה


# ============================== כלי עזר קטנים ==============================

def _yes_no_intent(text):
    """מזהה אם הודעה היא תשובה חיובית ("כן") או שלילית ("לא"), לפי התאמת מילים שלמות בלבד -
    לא התאמת תת-מחרוזת (כדי שמילה כמו "מוכן/ה" לא תזוהה בטעות כ"כן" בגלל שהיא מכילה את האותיות)."""
    words = re.findall(r"[\w֐-׿]+", text.strip().lower())   # פיצול ההודעה למילים נפרדות (כולל עברית)
    if any(word in _POSITIVE_WORDS for word in words):
        return "positive"
    if any(word in _NEGATIVE_WORDS for word in words):
        return "negative"
    return None                                                          # לא זוהתה תשובה חד-משמעית


def _digits_only(text):
    """משאיר רק ספרות ממחרוזת - כדי להשוות תעודות זהות בלי להיתקע על רווחים/מקפים."""
    return re.sub(r"\D", "", text or "")


def _looks_like_id_attempt(text):
    """מחליט האם הודעה היא בכלל ניסיון להזין תעודת זהות. חשוב מבחינת הוגנות כלפי המשתמש/ת:
    שאלה כמו 'למה אתם צריכים את זה?' לא תיחשב ניסיון אימות כושל ולא תבזבז לו/ה ניסיון יקר."""
    return len(_digits_only(text)) >= 5   # לפחות 5 ספרות ברצף - סביר שזה ניסיון להזין מספר זהות


def _name_matches(given_name, full_name):
    """בודק אם השם שהמשתמש/ת מסר/ה מתיישב עם השם המלא הרשום במערכת (לפחות מילה משותפת אחת).
    משמש כהגנת עומק: אם מישהו מוסר תעודת זהות אמיתית של אדם אחד אבל טוען שקוראים לו אחרת -
    לא נאמת אותו/ה אוטומטית."""
    given_words = set(re.findall(r"[\w֐-׿]+", (given_name or "").lower()))     # מילות השם שנמסר
    full_words = set(re.findall(r"[\w֐-׿]+", (full_name or "").lower()))         # מילות השם הרשום
    return bool(given_words & full_words)                                          # האם יש חפיפה כלשהי


def _format_date_he(iso_date):
    """ממיר תאריך מפורמט בסיס הנתונים (2026-10-05) לפורמט קריא בעברית (05.10.2026)."""
    parts = (iso_date or "").split("-")               # פיצול לשנה/חודש/יום
    if len(parts) != 3:                                  # אם הפורמט לא כצפוי - מחזירים כמו שהוא
        return iso_date
    return f"{parts[2]}.{parts[1]}.{parts[0]}"             # הרכבה מחדש בסדר יום.חודש.שנה


def _format_appointment_details(appt):
    """בונה מחרוזת תיאור קריאה לתור: תאריך, שעה, ורשימת השירותים."""
    services = appt.get("service_names") or "לא צוינו שירותים"
    return f"{_format_date_he(appt['appointment_date'])} בשעה {appt['appointment_time']} (שירותים: {services})"


# ============================== תשובות למידע ציבורי ==============================
# כל הפונקציות באזור הזה מחזירות מידע שממילא פומבי באתר (רשימת טיפולים, מחירים, שעות פתיחה),
# ולכן מותר להשיב עליהן בכל שלב של השיחה, גם לפני אימות. הן אינן נוגעות באף לקוח/ה מסוים/ת.

def _services_answer():
    """מחזיר את רשימת הטיפולים של הקליניקה, כולל משך ומחיר - מידע פומבי לחלוטין."""
    service_list = appointments.list_services()                     # שליפת השירותים מטבלת services
    if not service_list:                                               # מקרה קצה: אין שירותים מוגדרים
        return "רשימת הטיפולים אינה זמינה כרגע."
    lines = [                                                          # בניית שורה קריאה לכל שירות
        f"• {service['name']} - {service['default_duration_minutes']} דקות, "
        f"{int(service['default_price'])} ש\"ח"
        for service in service_list
    ]
    return "אלה הטיפולים שאנחנו מציעים:\n" + "\n".join(lines)


def _hours_answer():
    """מחזיר את שעות הפעילות וימי הפעילות של הקליניקה - מידע פומבי מקובץ ההגדרות."""
    open_days = ", ".join(_WEEKDAY_NAMES_HE[day] for day in config.OPEN_WEEKDAYS)   # תרגום מספרי הימים לשמות
    return (f"אנחנו פתוחים בימים {open_days}, "
            f"בין השעות {config.OPENING_TIME} ל-{config.CLOSING_TIME}.")


def _public_answer(intent):
    """מחזיר תשובה לשאלה 'ציבורית' (מחירים / שעות / קביעת תור / ברכה), או None אם הכוונה
    שזוהתה אינה אחת מאלה. מרוכז בפונקציה אחת כדי שכל שלבי השיחה יוכלו להשתמש בו באופן אחיד."""
    if intent == nlu.INTENT_SERVICE_INFO:
        return _services_answer()
    if intent == nlu.INTENT_OPENING_HOURS:
        return _hours_answer()
    if intent == nlu.INTENT_BOOK_NEW:
        return _BOOKING_HINT
    if intent == nlu.INTENT_GREETING:
        return ("שלום! אני העוזר/ת הדיגיטלי/ת של הקליניקה. אפשר לשאול אותי מתי התור שלך "
                "(אבקש שם ותעודת זהות לאימות), על מחירי טיפולים, או על שעות הפתיחה.")
    return None                                                     # לא שאלה ציבורית - הקוד הקורא ימשיך כרגיל


# ============================== שלב 1: זיהוי ראשוני ==============================

def _handle_start(state, user_message):
    """שלב הזיהוי הראשוני: קריאה ל-NLU (Gemini) לחילוץ כוונה/שם/תאריך/ת.ז., ואז ניתוב מתאים."""
    extracted = nlu.extract(user_message)                              # קריאת ה-API היחידה בשלב הזה
    if extracted is None:                                                 # אם החילוץ נכשל (שגיאת רשת/JSON לא תקין)
        return ("מצטערים, לא הצלחתי להבין את ההודעה כרגע. אפשר לנסח מחדש? "
                "לדוגמה: 'קוראים לי דנה ויש לי תור ב-15.03.2027'.")

    intent = extracted["intent"]                                        # הכוונה שזוהתה
    name = extracted["name"]                                              # השם שחולץ (או None)
    id_number = extracted["id_number"]                                      # תעודת זהות שחולצה (או None)
    if extracted["claimed_date"]:                                             # אם צוין תאריך בהודעה
        state["claimed_date"] = extracted["claimed_date"]                       # שומרים אותו להמשך השיחה

    if not name and not id_number:                                    # אם לא נמסר שום פרט מזהה בהודעה
        public = _public_answer(intent)                                  # אולי זו בכלל שאלה ציבורית
        if public:                                                          # אם כן - עונים עליה מיד
            return public
        if intent == nlu.INTENT_CANCEL:                                       # בקשת ביטול בלי זיהוי
            return "כדי לטפל בתור קיים אני צריך/ה קודם לזהות אותך. אפשר לכתוב את השם שלך?"
        return "כדי לבדוק את התור שלך אני צריך/ה לדעת מי את/ה. אפשר לכתוב את השם שלך?"

    if id_number:                                                     # מסלול מהיר: נמסרה תעודת זהות כבר עכשיו
        return _verify_with_id_number(state, id_number, claimed_name=name)

    matches = customers.search_customers_by_name(name)                 # חיפוש לקוחות תואמים לפי השם שחולץ

    if len(matches) == 0:                                                # 0 תוצאות - לא נחשף שום מידע נוסף
        return (f"לא מצאתי לקוח/ה בשם '{name}' במערכת שלנו. אפשר לבדוק את האיות ולנסות שוב? "
                f"אם עוד לא היית/ה אצלנו - {_BOOKING_HINT}")

    if len(matches) == 1:                                                # תוצאה אחת - ממשיכים לאישור השם המלא
        return _propose_candidate(state, matches[0])

    state["stage"] = STAGE_AWAIT_CLARIFY                                 # 2+ תוצאות - לא מנחשים, מבקשים הבהרה
    return f"מצאתי כמה לקוחות בשם '{name}' במערכת. אפשר לכתוב את השם המלא שלך (שם פרטי ושם משפחה)?"


def _propose_candidate(state, candidate):
    """שומר מועמד/ת יחיד/ה שנמצא/ה, ומבקש/ת אישור על השם המלא לפני שעוברים לשלב האימות."""
    state["candidate_customer_id"] = candidate["id"]     # שמירת המזהה בלבד - לא שום פרט אישי נוסף ב-state
    state["stage"] = STAGE_AWAIT_CONFIRM                    # מעבר לשלב אישור השם
    return f"קוראים לך {candidate['full_name']}?"


# ============================== שלב 2: הבהרה ואישור שם ==============================

def _handle_await_clarify(state, user_message):
    """שלב ההבהרה: המשתמש/ת אמור/ה לכתוב שם מלא, כדי לצמצם למועמד/ת יחיד/ה מתוך כמה התאמות."""
    matches = customers.search_customers_by_name(user_message)         # חיפוש ישיר לפי הטקסט שהוזן (בלי לבזבז קריאת API)

    if len(matches) == 1:                                                # הצטמצם למועמד/ת יחיד/ה - מצוין
        return _propose_candidate(state, matches[0])

    if len(matches) > 1:                                                 # עדיין יותר מדי התאמות
        return "עדיין נמצאו כמה התאמות. אפשר לכתוב שם מלא ומדויק יותר (שם פרטי ושם משפחה)?"

    extracted = nlu.extract(user_message)                              # 0 התאמות - אולי זו בכלל שאלה, לא שם
    if extracted:                                                         # אם החילוץ הצליח
        public = _public_answer(extracted["intent"])                        # אולי זו שאלה ציבורית
        if public:                                                             # אם כן - עונים ונשארים באותו שלב
            return public + "\n\nובחזרה לזיהוי: אפשר לכתוב את השם המלא שלך?"
        if extracted["id_number"]:                                           # אם נמסרה ת.ז. במקום שם
            return _verify_with_id_number(state, extracted["id_number"], claimed_name=extracted["name"])
        if extracted["name"]:                                                  # אם חולץ שם מתוך משפט שלם
            by_name = customers.search_customers_by_name(extracted["name"])       # ננסה לחפש לפיו
            if len(by_name) == 1:
                return _propose_candidate(state, by_name[0])

    return "עדיין לא הצלחתי למצוא לקוח/ה בשם הזה. אפשר לכתוב את השם המלא בדיוק כפי שהוא רשום אצלנו?"


def _handle_await_confirm(state, user_message):
    """שלב אישור הזהות בשם: המשתמש/ת מאשר/ת או מכחיש/ה שזה השם המלא שלו/ה, לפני שמבקשים ת.ז."""
    intent = _yes_no_intent(user_message)                              # בדיקה מקומית מהירה, בלי קריאת API

    if intent == "positive":
        state["stage"] = STAGE_AWAIT_ID                                     # מעבר לשלב האימות עצמו
        return "מה תעודת הזהות שלך? (לצורך אימות בלבד)"

    if intent == "negative":
        state["candidate_customer_id"] = None                               # ביטול המועמד/ת השגוי/ה
        state["stage"] = STAGE_START                                          # חזרה לשלב הזיהוי מההתחלה
        return "אין בעיה, מצטערים על הטעות. אפשר לכתוב מה השם המלא שלך?"

    if _looks_like_id_attempt(user_message):                           # המשתמש/ת "קפץ/ה קדימה" והזין/ה ת.ז.
        state["stage"] = STAGE_AWAIT_ID                                     # מקבלים את זה בהבנה, ומטפלים כאימות
        return _handle_await_id(state, user_message)

    extracted = nlu.extract(user_message)                              # לא כן/לא ולא ת.ז. - אולי זו שאלה
    if extracted:
        public = _public_answer(extracted["intent"])                       # אולי זו שאלה ציבורית
        if public:
            return public + "\n\nובחזרה לזיהוי: השם שהצגתי הוא הנכון? (כן / לא)"
        if extracted["name"]:                                               # אולי נמסר שם אחר, מלא יותר
            matches = customers.search_customers_by_name(extracted["name"])
            if len(matches) == 1:
                return _propose_candidate(state, matches[0])

    return "לא הבנתי - זה נכון או לא נכון? אפשר לענות 'כן' או 'לא'."      # תשובה לא ברורה - שואלים שוב


# ============================== שלב 3: אימות תעודת זהות ==============================

def _spend_attempt(state):
    """מפחית ניסיון אימות אחד, ומחזיר True אם נגמרו הניסיונות (כלומר צריך לחסום את השיחה).
    מרוכז בפונקציה אחת כדי שכל מסלולי הכישלון יתנהגו בדיוק אותו דבר - בלי פרצות."""
    state["attempts_left"] -= 1                          # ניצול ניסיון אחד
    if state["attempts_left"] <= 0:                         # אם נגמרו הניסיונות המותרים
        state["stage"] = STAGE_BLOCKED                         # חסימת השיחה
        state["candidate_customer_id"] = None                    # מחיקת המועמד/ת מה-state, ליתר ביטחון
        return True
    return False


def _blocked_message():
    """הודעת החסימה האחידה, אחרי ניצול כל ניסיונות האימות."""
    return ("תעודת הזהות שהוזנה שגויה, ונוצל מספר הניסיונות המרבי המותר. "
            "מטעמי אבטחה השיחה נחסמת כעת. אפשר לפנות אלינו טלפונית, או להתחיל שיחה חדשה.")


def _verify_with_id_number(state, id_number, claimed_name=None):
    """מסלול אימות ישיר לפי תעודת זהות שנמסרה בהודעה חופשית (בלי שעברנו קודם דרך חיפוש שם).
    תעודת הזהות היא סוד שרק בעל/ת התעודה אמור/ה להכיר - ולכן התאמה מדויקת שלה מהווה אימות
    לגיטימי בפני עצמו. עדיין: כל ניסיון כושל צורך ניסיון מהמכסה, כדי שלא ניתן יהיה לנחש
    תעודות זהות באופן שיטתי."""
    customer = customers.find_customer_by_id_number(id_number)      # חיפוש לקוח/ה עם תעודת הזהות הזו

    if customer is None:                                               # התעודה כלל לא רשומה במערכת
        if _spend_attempt(state):                                         # גם ניחוש כזה עולה ניסיון (הגנה מפני סריקה)
            return _blocked_message()
        return ("לא נמצא/ה במערכת שלנו לקוח/ה עם תעודת הזהות הזו. "
                "אם זו הפעם הראשונה שלך אצלנו - נשמח לארח אותך! "
                f"{_BOOKING_HINT} אם את/ה בטוח/ה שכבר ביקרת אצלנו, אפשר לבדוק את המספר ולנסות שוב.")

    if claimed_name and not _name_matches(claimed_name, customer["full_name"]):
        # הגנת עומק: נמסרה תעודת זהות אמיתית, אבל בשם שאינו מתיישב עם הרשום עליה במערכת
        if _spend_attempt(state):
            return _blocked_message()
        return "הפרטים שנמסרו אינם מתיישבים זה עם זה. אפשר לבדוק את השם ואת מספר תעודת הזהות ולנסות שוב?"

    state["candidate_customer_id"] = customer["id"]     # מרגע זה זהו הלקוח/ה של השיחה
    state["stage"] = STAGE_VERIFIED                       # האימות הצליח
    return _build_verified_reply(state, customer)           # רק כאן, אחרי אימות, ניגשים למידע על תורים


def _handle_await_id(state, user_message):
    """שלב האימות עצמו: השוואת תעודת הזהות שהוזנה מול הערך השמור בבסיס הנתונים עבור המועמד/ת.
    זהו השלב הקריטי מבחינת אבטחה - שום מידע אישי אינו נגיש כאן, אלא רק לאחר התאמה מדויקת."""
    candidate = customers.get_customer(state["candidate_customer_id"])   # שליפת פרטי המועמד/ת (כולל id_number)
    if not candidate:                                                      # הגנה (למשל אם הלקוח/ה נמחק/ה באמצע השיחה)
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        return "משהו השתבש באיתור הפרטים שלך. אפשר להתחיל שוב ולכתוב את השם שלך?"

    if not _looks_like_id_attempt(user_message):        # ההודעה אינה בכלל ניסיון להזין תעודת זהות
        extracted = nlu.extract(user_message)              # אולי זו שאלה לגיטימית ("למה אתם צריכים את זה?")
        if extracted:
            public = _public_answer(extracted["intent"])      # אם זו שאלה ציבורית - עונים בלי לגבות ניסיון
            if public:
                return public + "\n\nכדי להמשיך לתור שלך, אפשר להזין את מספר תעודת הזהות."
        # חשוב: לא מנצלים כאן ניסיון אימות - כי המשתמש/ת בכלל לא ניסה/תה לנחש מספר
        return ("כדי להגן על הפרטיות שלך אני חייב/ת לאמת את זהותך לפני שאוכל למסור פרטים על תורים. "
                "אפשר להזין את מספר תעודת הזהות (9 ספרות)?")

    entered_id = _digits_only(user_message)                              # הספרות שהוזנו על ידי המשתמש/ת בלבד
    real_id = _digits_only(candidate.get("id_number") or "")               # הספרות בתעודת הזהות השמורה במערכת

    if real_id and entered_id == real_id:                                 # התאמה מדויקת - ורק זה מאפשר המשך
        state["stage"] = STAGE_VERIFIED
        return _build_verified_reply(state, candidate)                       # רק כאן, אחרי אימות, ניגשים למידע אישי

    # מכאן - האימות נכשל. בודקים אם התעודה בכלל קיימת במערכת, כדי לתת תשובה מועילה יותר,
    # מבלי לחשוף אי פעם למי היא שייכת אם היא שייכת למישהו אחר.
    other_owner = customers.find_customer_by_id_number(entered_id)        # האם מישהו במערכת רשום עם התעודה הזו?

    if _spend_attempt(state):                                              # כל כישלון עולה ניסיון, בלי יוצא מן הכלל
        return _blocked_message()

    remaining = state["attempts_left"]                                     # מספר הניסיונות שעוד נותרו
    if other_owner is None:                                                  # התעודה כלל לא רשומה אצלנו
        return (f"תעודת הזהות הזו אינה רשומה במערכת שלנו. "
                f"אם זו הפעם הראשונה שלך אצלנו - {_BOOKING_HINT} "
                f"אם כבר ביקרת אצלנו, אפשר לבדוק את המספר ולנסות שוב (נותרו {remaining} ניסיונות).")

    # התעודה כן קיימת במערכת, אבל שייכת ללקוח/ה אחר/ת מזה שהשם שלו/ה אושר בשיחה.
    # לא חושפים בשום אופן למי היא שייכת - רק מציינים שאין התאמה.
    return (f"מספר תעודת הזהות שהוזן אינו תואם ללקוח/ה בשם {candidate['full_name']}. "
            f"נותרו {remaining} ניסיונות.")


# ============================== שלב 4: תשובה מבוססת דאטה אמיתי ==============================

def _load_verified_appointment(state):
    """הפונקציה היחידה בקובץ הזה שניגשת למידע על תורים. היא מתחילה בבדיקת _assert_verified,
    כך שגישה למידע אישי לפני אימות מוצלח תיפול בשגיאה במקום לדלוף החוצה."""
    _assert_verified(state)                                                 # שומר הסף של דרישת האבטחה
    return appointments.get_next_upcoming_appointment(state["candidate_customer_id"])


def _build_verified_reply(state, candidate):
    """נקראת אך ורק אחרי אימות תעודת זהות מוצלח: שולפת את התור העתידי האמיתי של הלקוח/ה,
    ומנסחת תשובה שמדגישה פער מול התאריך שהלקוח/ה טען/ה - או מאשרת, אם אין פער או אין טענה."""
    appt = _load_verified_appointment(state)                              # שליפת התור האמיתי - דרך שומר הסף
    claimed_date = state.get("claimed_date")                                # התאריך שהלקוח/ה טען/ה, מתחילת השיחה

    if not appt:                                                             # ללקוח/ה קיים/ת בלי אף תור פתוח
        return (f"מצאתי אותך, {candidate['full_name']}! "
                f"לא נמצא לך כרגע אף תור עתידי פתוח במערכת. {_BOOKING_HINT}")

    real_date = appt["appointment_date"]                                     # התאריך האמיתי של התור

    if claimed_date and claimed_date != real_date:                            # יש פער בין הטענה למציאות - צריך תיקון
        phrased = _phrase_correction(candidate["full_name"], claimed_date, appt)  # ניסוח טבעי עם Gemini
        if phrased:                                                                # אם הניסוח הצליח
            return phrased
        details = _format_appointment_details(appt)                                 # גיבוי: תבנית קבועה אם Gemini נכשל
        return (f"מצאתי אותך, {candidate['full_name']}! שימו לב: התור שלך בפועל הוא ב-{details} "
                f"(ולא ב-{_format_date_he(claimed_date)} כפי שציינת).")

    details = _format_appointment_details(appt)                               # אין פער (או שלא צוין תאריך) - מאשרים
    return f"מצאתי אותך, {candidate['full_name']}! התור שלך הוא ב-{details}."


def _phrase_correction(full_name, claimed_date, appt):
    """קריאת API אחת ויחידה ל-Gemini, רק כדי לנסח בעברית טבעית את פער התאריכים - אחרי שכל
    הנתונים האמיתיים כבר בידינו. Gemini מנסח משפט, לא ממציא נתונים (ראו System Instruction).
    שימו לב: ההודעה המקורית של המשתמש/ת לא נשלחת לכאן בכלל - רק עובדות מבסיס הנתונים - וכך
    לא ניתן להזריק דרך הצ'אט הוראות שישנו את התנהגות המודל בשלב הרגיש הזה."""
    facts = (
        f"שם הלקוח/ה: {full_name}. "
        f"התאריך שהלקוח/ה טען/ה: {_format_date_he(claimed_date)}. "
        f"התאריך האמיתי של התור: {_format_date_he(appt['appointment_date'])}. "
        f"השעה האמיתית: {appt['appointment_time']}. "
        f"השירותים בתור: {appt.get('service_names') or 'לא צוינו'}."
    )
    return gemini_client.generate_text(_PHRASING_SYSTEM_INSTRUCTION, facts)


def _handle_verified(state, user_message):
    """שיחה אחרי אימות מוצלח: אפשר לענות על שאלות המשך - אבל אך ורק על הלקוח/ה שאומת/ה
    בשיחה הזו. בקשה למידע על אדם אחר לעולם לא תיענה, כי כל השליפות כאן משתמשות במזהה
    ה-candidate_customer_id השמור ב-state, ולא בשום שם שיופיע בהודעה החדשה."""
    customer = customers.get_customer(state["candidate_customer_id"])   # הלקוח/ה שאומת/ה בשיחה הזו
    if not customer:                                                      # הגנה תיאורטית
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        return "משהו השתבש. אפשר להתחיל שוב ולכתוב את השם שלך?"

    extracted = nlu.extract(user_message)                              # הבנת שאלת ההמשך
    if extracted is None:                                                 # אם החילוץ נכשל
        return "לא הצלחתי להבין את השאלה. אפשר לנסח מחדש?"

    intent = extracted["intent"]                                        # הכוונה שזוהתה

    public = _public_answer(intent)                                     # שאלות ציבוריות - נענות כרגיל
    if public:
        return public

    if intent == nlu.INTENT_CANCEL:                                      # בקשת ביטול תור
        appt = _load_verified_appointment(state)                            # שליפה מאובטחת של התור
        if not appt:
            return "לא נמצא לך תור עתידי פתוח לביטול."
        return (f"התור שלך הוא ב-{_format_appointment_details(appt)}. "
                "לביטול אפשר להיכנס למסך 'התורים שלי' בתפריט למעלה ולהזין את מספר הטלפון שלך, "
                "או להתקשר אלינו.")

    if extracted["name"] and not _name_matches(extracted["name"], customer["full_name"]):
        # שאלה על אדם אחר - סירוב מנומס. אין כאן שום שליפה של מידע על אותו אדם.
        return ("אני יכול/ה למסור מידע רק על התור של מי שאומת/ה בשיחה הזו. "
                "כדי לבדוק תור של אדם אחר, יש להתחיל שיחה חדשה ולעבור אימות בנפרד.")

    if extracted["claimed_date"]:                                        # הלקוח/ה מציע/ה תאריך אחר לבדיקה
        state["claimed_date"] = extracted["claimed_date"]                   # מעדכנים ומשיבים מחדש עם השוואה
        return _build_verified_reply(state, customer)

    if intent == nlu.INTENT_CHECK_APPOINTMENT:                           # בקשה לראות שוב את פרטי התור
        return _build_verified_reply(state, customer)

    return ("אפשר לשאול אותי על התור שלך, על מחירי הטיפולים או על שעות הפתיחה. "
            "לבדיקת תור של אדם אחר - יש להתחיל שיחה חדשה.")
