"""
chatbot/conversation.py - מוח הצ'אטבוט: מנהל את מצב השיחה (מי המועמד/ת הנוכחי/ת, האם הוא/היא
כבר אומת/ה, כמה ניסיונות אימות נותרו) ומממש שלב-אחר-שלב את הזרימה: זיהוי -> הבהרה (אם צריך)
-> אימות תעודת זהות -> תשובה מבוססת דאטה אמיתי.

כל הפונקציות כאן מקבלות ומחזירות את מצב השיחה (state) כ-dict רגיל של פייתון - אין כאן שום
תלות ב-Flask עצמו. routes/chatbot_routes.py הוא זה ששומר את ה-dict הזה בפועל בתוך session
של Flask, בין הודעה להודעה. ההפרדה הזו הופכת את לוגיקת השיחה לקלה יותר להבנה ולבדיקה.

דרישת האבטחה הקשיחה של הפרויקט: לפני אימות תעודת זהות מוצלח (stage == STAGE_VERIFIED),
אף פונקציה כאן לא ניגשת בכלל למידע על תורים (models.appointments) - לא רק "יוצא ככה במקרה".
אפשר לוודא זאת ישירות בקוד: הקריאה היחידה ל-appointments.get_next_upcoming_appointment
נמצאת בתוך _build_verified_reply, שנקראת אך ורק אחרי שהתעודת זהות כבר הושוותה ונמצאה תואמת.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import re   # לזיהוי מילות כן/לא ולחילוץ ספרות מתוך הודעות המשתמש

import config                          # קובץ ההגדרות (מספר ניסיונות האימות המותר)
import models.customers as customers     # חיפוש לקוח לפי שם, שליפת פרטי לקוח (כולל id_number)
import models.appointments as appointments  # שליפת התור העתידי האמיתי - רק אחרי אימות!
import chatbot.nlu as nlu                     # חילוץ שם+תאריך מהודעה חופשית (Gemini, קריאה #1)
import chatbot.gemini_client as gemini_client   # ניסוח התשובה הסופית בשפה טבעית (Gemini, קריאה #2)

# שלבי השיחה האפשריים - "מכונת מצבים" (state machine) פשוטה
STAGE_START = "start"                     # עדיין לא ידוע מי המשתמש/ת
STAGE_AWAIT_CONFIRM = "await_confirm"     # נמצא לקוח יחיד תואם, מחכים לאישור "כן/לא זה אני"
STAGE_AWAIT_CLARIFY = "await_clarify"     # נמצאו כמה לקוחות תואמים, מחכים לשם מלא לצורך הבהרה
STAGE_AWAIT_ID = "await_id"               # מחכים להזנת תעודת זהות לצורך אימות
STAGE_VERIFIED = "verified"               # הזהות אומתה בהצלחה בשיחה הזו
STAGE_BLOCKED = "blocked"                 # נחסם אחרי יותר מדי ניסיונות אימות כושלים

_POSITIVE_WORDS = {"כן", "נכון", "אכן", "בדיוק", "yes", "yep"}   # מילים שמזוהות כתשובה חיובית
_NEGATIVE_WORDS = {"לא", "לא נכון", "no", "nope"}                  # מילים שמזוהות כתשובה שלילית

_PHRASING_SYSTEM_INSTRUCTION = (
    "את/ה עוזר/ת קליניקת קוסמטיקה ידידותי/ת, שמנוסח/ת בעברית תקנית. תקבל/י עובדות אמיתיות על "
    "תור של לקוח/ה, ואת התאריך שהלקוח/ה חשב/ה (בטעות) שהתור נמצא בו. נסח/י משפט אחד או שניים "
    "קצרים, אדיבים וברורים בעברית, שמודיעים ללקוח/ה מה התאריך והשעה האמיתיים של התור (ואילו "
    "שירותים כלולים בו), ומציינים בעדינות שהתאריך שהוא/היא ציין/ה שגוי. "
    "חשוב מאוד: אסור לך להמציא, לשנות או לנחש שום נתון - יש להשתמש אך ורק בדיוק בעובדות שסופקו "
    "לך בהודעה. אל תוסיף/י ברכות מיותרות או הקדמות ארוכות - ישר לעניין."
)


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
        reply = "כבר אימתנו את הזהות שלך בשיחה הזו. לבדיקת תור של אדם אחר, אפשר ללחוץ על 'שיחה חדשה'."
    else:                                                                  # מצב לא צפוי (הגנה תיאורטית בלבד)
        reply = "משהו השתבש. אפשר ללחוץ על 'שיחה חדשה' ולנסות שוב?"

    state["history"].append({"role": "bot", "text": reply})             # שמירת תשובת הבוט בתמלול השיחה
    return state, reply                                                    # החזרת המצב המעודכן והתשובה לקריאה


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


def _format_appointment_details(appt):
    """בונה מחרוזת תיאור קריאה לתור: תאריך, שעה, ורשימת השירותים."""
    services = appt.get("service_names") or "לא צוינו שירותים"
    return f"{appt['appointment_date']} בשעה {appt['appointment_time']} (שירותים: {services})"


def _handle_start(state, user_message):
    """שלב הזיהוי הראשוני: קריאה ל-NLU (Gemini) לחילוץ שם ותאריך נטען, ואז חיפוש לקוח לפי השם."""
    extracted = nlu.extract_name_and_date(user_message)                 # קריאת ה-API היחידה בשלב הזה
    if extracted is None:                                                  # אם החילוץ נכשל (שגיאת רשת/JSON לא תקין)
        return ("מצטערים, לא הצלחתי להבין את ההודעה כרגע. אפשר לנסח מחדש? "
                "לדוגמה: 'קוראים לי דנה ויש לי תור ב-15.03.2027'.")

    name = extracted.get("name")                                          # השם שחולץ מההודעה (או None)
    state["claimed_date"] = extracted.get("claimed_date")                    # שמירת התאריך הנטען למאוחר יותר בשיחה

    if not name:                                                           # אם לא זוהה שום שם בהודעה
        return "לא הצלחתי להבין מה השם שלך. אפשר לכתוב את השם שלך?"

    matches = customers.search_customers_by_name(name)                     # חיפוש לקוחות תואמים לפי השם שחולץ

    if len(matches) == 0:                                                    # 0 תוצאות - לא נחשף שום מידע נוסף
        return f"לא מצאתי לקוח/ה בשם '{name}' במערכת שלנו. אפשר לבדוק את האיות ולנסות שוב?"

    if len(matches) == 1:                                                    # תוצאה אחת - ממשיכים לאימות שם מלא
        candidate = matches[0]
        state["candidate_customer_id"] = candidate["id"]                       # שמירת מזהה המועמד/ת בלבד (לא שום פרט אישי נוסף)
        state["stage"] = STAGE_AWAIT_CONFIRM
        return f"קוראים לך {candidate['full_name']}?"

    state["stage"] = STAGE_AWAIT_CLARIFY                                     # 2+ תוצאות - לא מנחשים, מבקשים הבהרה
    return f"מצאתי כמה לקוחות בשם '{name}' במערכת. אפשר לכתוב את השם המלא שלך (שם פרטי ושם משפחה)?"


def _handle_await_clarify(state, user_message):
    """שלב ההבהרה: המשתמש/ת אמור/ה לכתוב שם מלא, כדי לצמצם למועמד/ת יחיד/ה מתוך כמה התאמות."""
    matches = customers.search_customers_by_name(user_message)             # חיפוש חוזר, הפעם לפי השם המלא שהוזן

    if len(matches) == 1:                                                    # הצטמצם למועמד/ת יחיד/ה - מצוין
        candidate = matches[0]
        state["candidate_customer_id"] = candidate["id"]
        state["stage"] = STAGE_AWAIT_CONFIRM
        return f"קוראים לך {candidate['full_name']}?"

    if len(matches) == 0:                                                    # השם המלא שהוזן לא נמצא בכלל
        return "עדיין לא הצלחתי למצוא לקוח/ה בשם הזה. אפשר לכתוב את השם המלא בדיוק כפי שהוא רשום אצלנו?"

    return "עדיין נמצאו כמה התאמות. אפשר לכתוב שם מלא ומדויק יותר (שם פרטי ושם משפחה)?"


def _handle_await_confirm(state, user_message):
    """שלב אישור הזהות בשם: המשתמש/ת מאשר/ת או מכחיש/ה שזה השם המלא שלו/ה, לפני שמבקשים ת.ז."""
    intent = _yes_no_intent(user_message)

    if intent == "positive":
        state["stage"] = STAGE_AWAIT_ID                                        # מעבר לשלב האימות עצמו
        state["attempts_left"] = config.CHATBOT_MAX_VERIFICATION_ATTEMPTS         # איפוס מונה הניסיונות לקראת האימות
        return "מה תעודת הזהות שלך? (לצורך אימות בלבד)"

    if intent == "negative":
        state["candidate_customer_id"] = None                                  # ביטול המועמד/ת השגוי/ה
        state["stage"] = STAGE_START                                             # חזרה לשלב הזיהוי מההתחלה
        return "אין בעיה, מצטערים על הטעות. אפשר לכתוב מה השם המלא שלך?"

    return "לא הבנתי - זה נכון או לא נכון? אפשר לענות 'כן' או 'לא'."               # תשובה לא ברורה - שואלים שוב


def _handle_await_id(state, user_message):
    """שלב האימות עצמו: השוואת תעודת הזהות שהוזנה מול הערך השמור בבסיס הנתונים עבור המועמד/ת.
    זהו השלב הקריטי מבחינת אבטחה - שום מידע על תורים לא נגיש כאן, רק לאחר התאמה מדויקת."""
    candidate = customers.get_customer(state["candidate_customer_id"])     # שליפת פרטי המועמד/ת (כולל id_number)
    if not candidate:                                                        # הגנה תיאורטית (למשל אם הלקוח נמחק באמצע השיחה)
        state["stage"] = STAGE_START
        state["candidate_customer_id"] = None
        return "משהו השתבש באיתור הפרטים שלך. אפשר להתחיל שוב ולכתוב את השם שלך?"

    entered_id = _digits_only(user_message)                                  # הספרות שהוזנו על ידי המשתמש/ת בלבד
    real_id = _digits_only(candidate.get("id_number") or "")                   # הספרות בתעודת הזהות השמורה במערכת

    if real_id and entered_id and entered_id == real_id:                      # התאמה מדויקת - ורק זה מאפשר המשך
        state["stage"] = STAGE_VERIFIED
        return _build_verified_reply(state, candidate)                          # רק כאן, אחרי אימות, נגישים לתור האמיתי

    state["attempts_left"] -= 1                                               # ניסיון כושל - הפחתת מונה הניסיונות
    if state["attempts_left"] <= 0:                                             # נגמרו הניסיונות המותרים
        state["stage"] = STAGE_BLOCKED
        return ("תעודת הזהות שהוזנה שגויה, ונוצל מספר הניסיונות המרבי המותר. "
                "מטעמי אבטחה השיחה נחסמת כעת. אפשר לפנות אלינו טלפונית, או להתחיל שיחה חדשה.")

    remaining = state["attempts_left"]                                          # מספר הניסיונות שעוד נותרו
    return f"תעודת הזהות שהוזנה אינה תואמת. נותרו {remaining} ניסיונות. אפשר לנסות שוב?"


def _build_verified_reply(state, candidate):
    """נקראת אך ורק אחרי אימות תעודת זהות מוצלח: שולפת את התור העתידי האמיתי של הלקוח/ה,
    ומנסחת תשובה שמדגישה פער מול התאריך שהלקוח/ה טען/ה - או מאשרת, אם אין פער או אין טענה."""
    appt = appointments.get_next_upcoming_appointment(candidate["id"])      # שליפת התור האמיתי - כאן ורק כאן!
    claimed_date = state.get("claimed_date")                                  # התאריך שהלקוח/ה טען/ה, מתחילת השיחה

    if not appt:                                                                # ללקוח/ה קיים/ת בלי אף תור פתוח
        return f"מצאתי אותך, {candidate['full_name']}! אבל לא נמצא לך/ך כרגע אף תור פתוח במערכת."

    real_date = appt["appointment_date"]                                        # התאריך האמיתי של התור

    if claimed_date and claimed_date != real_date:                               # יש פער בין הטענה למציאות - צריך תיקון
        phrased = _phrase_correction(candidate["full_name"], claimed_date, appt)    # ניסוח טבעי עם Gemini (קריאה #2)
        if phrased:                                                                   # אם הניסוח הצליח
            return phrased
        details = _format_appointment_details(appt)                                    # גיבוי: תבנית קבועה אם Gemini נכשל
        return (f"מצאתי אותך, {candidate['full_name']}! שימו לב: התור שלך בפועל הוא ב-{details} "
                f"(ולא ב-{claimed_date} כפי שציינת).")

    details = _format_appointment_details(appt)                                  # אין פער (או שלא צוין תאריך כלל) - מאשרים
    return f"מצאתי אותך, {candidate['full_name']}! התור שלך הוא ב-{details}."


def _phrase_correction(full_name, claimed_date, appt):
    """קריאת API אחת ויחידה ל-Gemini, רק כדי לנסח בעברית טבעית את פער התאריכים - אחרי שכל
    הנתונים האמיתיים כבר בידינו. Gemini מנסח משפט, לא ממציא נתונים (ראו System Instruction)."""
    facts = (
        f"שם הלקוח/ה: {full_name}. "
        f"התאריך שהלקוח/ה טען/ה: {claimed_date}. "
        f"התאריך האמיתי של התור: {appt['appointment_date']}. "
        f"השעה האמיתית: {appt['appointment_time']}. "
        f"השירותים בתור: {appt.get('service_names') or 'לא צוינו'}."
    )
    return gemini_client.generate_text(_PHRASING_SYSTEM_INSTRUCTION, facts)
