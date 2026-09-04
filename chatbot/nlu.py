"""
chatbot/nlu.py - שכבת ה-NLU (הבנת שפה טבעית) של הצ'אטבוט: לוקחת משפט חופשי שהלקוח/ה כתב/ה,
ומחלצת ממנו שני פרטים בלבד - שם שהמשתמש ציין לעצמו, ותאריך תור שהוא/היא טוען/ת שיש לו/ה.
זו לא "שיחה" עם Gemini - זו קריאת API אחת שמחזירה JSON קבוע ומובנה, כדי שאפשר יהיה לפרסר
אותו בביטחון בקוד רגיל (בלי לנחש מה המודל "התכוון" להגיד).
"""

import re                                  # לוולידציה של פורמט התאריך שחוזר מ-Gemini, בלי לבזבז קריאת API נוספת

import chatbot.gemini_client as gemini_client   # העטיפה סביב ה-SDK של Gemini

# הוראת המערכת הקשיחה: מגדירה בדיוק מה על Gemini לחלץ, ובאיזה פורמט להחזיר אותו.
_SYSTEM_INSTRUCTION = (
    "את/ה מנוע חילוץ מידע (extraction) עבור צ'אטבוט של קליניקת קוסמטיקה. "
    "תפקידך היחיד: לקרוא הודעה חופשית שכתב/ה לקוח/ה, ולחלץ ממנה שני פרטים בלבד - "
    "1) name: שם (פרטי או מלא) שהלקוח/ה ציין/ה עבור עצמו/ה. "
    "2) claimed_date: תאריך תור שהלקוח/ה טוען/ת שיש לו/ה, מומר לפורמט 'YYYY-MM-DD'. "
    "אם המשתמש כתב תאריך בכל פורמט אחר (כמו 18.01.2027 או 18/1/2027) - המר אותו ל-YYYY-MM-DD. "
    "אם שם לא הוזכר בהודעה - name יהיה null. אם תאריך לא הוזכר - claimed_date יהיה null. "
    "אל תמציא/י שם או תאריך שלא הוזכרו במפורש בהודעה. אל תוסיף/י שום שדה נוסף, "
    "ואל תסביר/י את התשובה - רק את אובייקט ה-JSON עצמו."
)

# הסכמה שמכריחה את Gemini להחזיר בדיוק את שני השדות האלה, בלי שום דבר נוסף
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "nullable": True},
        "claimed_date": {"type": "STRING", "nullable": True},
    },
    "required": ["name", "claimed_date"],
}


def extract_name_and_date(free_text_message):
    """מחלצת שם ותאריך נטען (claimed_date) מתוך הודעה חופשית, בעזרת קריאת API אחת ל-Gemini.
    מחזירה dict בפורמט {"name": ..., "claimed_date": ...} (כל שדה יכול להיות None), או None אם
    הייתה שגיאה כלשהי (כדי שהקוד הקורא יבקש מהמשתמש לנסח מחדש, בלי לקרוס ובלי לקרוא ל-API שוב)."""
    if not free_text_message or not free_text_message.strip():   # הודעה ריקה - אין טעם לבזבז קריאת API עליה כלל
        return {"name": None, "claimed_date": None}                 # מחזירים תוצאה ריקה ישירות, בלי לפנות ל-Gemini

    result = gemini_client.extract_json(                          # קריאת ה-API היחידה של הפונקציה הזו
        system_instruction=_SYSTEM_INSTRUCTION,
        user_message=free_text_message,
        response_schema=_RESPONSE_SCHEMA,
    )

    if not isinstance(result, dict):                                # אם הקריאה נכשלה (None) או החזירה משהו לא תקין
        return None                                                    # מאותתים לקוד הקורא שהחילוץ נכשל

    name = result.get("name")                                       # שליפת השם שחולץ (או None)
    claimed_date = result.get("claimed_date")                         # שליפת התאריך שחולץ (או None)
    if isinstance(claimed_date, str) and not re.match(r"^\d{4}-\d{2}-\d{2}$", claimed_date):
        claimed_date = None   # הגנה נוספת: אם התאריך לא בפורמט YYYY-MM-DD תקין, מתעלמים ממנו במקום לקרוס בהמשך

    return {"name": name, "claimed_date": claimed_date}                # החזרת התוצאה הנקייה לקוד הקורא
