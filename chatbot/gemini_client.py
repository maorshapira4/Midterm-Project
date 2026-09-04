"""
chatbot/gemini_client.py - עטיפה דקה סביב ה-SDK הרשמי של Gemini (google-genai).
כל שאר קבצי הצ'אטבוט (nlu.py, conversation.py) קוראים ל-Gemini רק דרך שתי הפונקציות
שבקובץ הזה, ולא ניגשים ל-SDK ישירות. זה נותן לנו מקום אחד ויחיד לטפל בשגיאות רשת/מכסה,
ומקום אחד לוודא שאנחנו לא מבזבזים קריאות API בטייר החינמי לשווא.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import json   # לפענוח תשובות JSON שחוזרות מ-Gemini

import config   # קובץ ההגדרות הכלליות - כאן נמצא מפתח ה-API ושם המודל

from google import genai            # ה-SDK הרשמי של Google ל-Gemini API
from google.genai import types      # טיפוסי ההגדרות (GenerateContentConfig וכו') של ה-SDK

_client = None   # מופע יחיד (singleton) של הלקוח, כדי לא ליצור חיבור חדש בכל קריאה


def _get_client():
    """מחזיר מופע לקוח Gemini יחיד, ויוצר אותו רק בפעם הראשונה שהוא נדרש (lazy init).
    זורק שגיאה ברורה אם מפתח ה-API לא הוגדר בכלל - כדי שהבעיה תתגלה מיד, לא רק כשמישהו כותב הודעה."""
    global _client                                                # שימוש במשתנה הגלובלי שמוגדר מעל
    if _client is None:                                              # אם עדיין לא נוצר לקוח בפעם הזו שהאפליקציה רצה
        if not config.GEMINI_API_KEY:                                    # אם מפתח ה-API חסר לגמרי
            raise RuntimeError(                                             # שגיאה ברורה שמסבירה בדיוק מה חסר ואיך לתקן
                "משתנה הסביבה GEMINI_API_KEY לא מוגדר. הגדירו אותו בקובץ .env מקומי (ראו .env.example) "
                "או כמשתנה סביבה אמיתי בשרת (ראו DEPLOY.md)."
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)              # יצירת הלקוח פעם אחת בלבד
    return _client                                                      # החזרת הלקוח (הקיים או שנוצר הרגע)


def extract_json(system_instruction, user_message, response_schema):
    """קריאת API אחת ל-Gemini, במצב JSON מובנה (structured output) - מכריחה את המודל להחזיר
    אך ורק JSON שתואם את הסכמה שהתקבלה, ולא טקסט חופשי. מחזירה dict מפוענח בהצלחה, או None אם
    הייתה שגיאה כלשהי (ברשת, במכסה, או JSON לא תקין) - כדי שהקוד הקורא יוכל להגיב בעדינות
    (לבקש מהמשתמש לנסח מחדש) במקום לקרוס. לא מבצעת ניסיון חוזר אוטומטי - כדי לא לכפול קריאות API."""
    try:
        client = _get_client()                                          # קבלת הלקוח (או יצירתו אם עוד לא נוצר)
        response = client.models.generate_content(                        # קריאת ה-API היחידה של הפונקציה הזו
            model=config.GEMINI_MODEL_NAME,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,                       # הוראת המערכת הקשיחה (מוגדרת בקובץ הקורא)
                response_mime_type="application/json",                          # מכריח תשובה בפורמט JSON בלבד
                response_schema=response_schema,                                   # מכריח את המבנה המדויק של ה-JSON
                temperature=0,                                                        # ללא "יצירתיות" - חילוץ עובדתי ועקבי
            ),
        )
        return json.loads(response.text)                                    # פענוח הטקסט שחזר לאובייקט פייתון (dict)
    except Exception:                                                     # כל שגיאה (רשת/מכסה/JSON לא תקין/מפתח שגוי וכו')
        return None                                                          # מחזירים None - הקוד הקורא יטפל בעדינות


def generate_text(system_instruction, user_message):
    """קריאת API אחת ל-Gemini לניסוח תשובה טבעית בעברית (לא JSON) - משמשת רק לניסוח התשובה
    הסופית עם הפער בין התאריך שהלקוח/ה טען/ה לבין התאריך האמיתי, אחרי שכבר יש בידינו את כל
    הנתונים האמיתיים. מחזירה מחרוזת טקסט, או None אם הייתה שגיאה."""
    try:
        client = _get_client()                                          # קבלת הלקוח (או יצירתו אם עוד לא נוצר)
        response = client.models.generate_content(                        # קריאת ה-API היחידה של הפונקציה הזו
            model=config.GEMINI_MODEL_NAME,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,                       # הוראת המערכת (מוגדרת בקובץ הקורא)
                temperature=0.3,                                                # מעט "טבעיות" בניסוח, אך לא מוגזם
            ),
        )
        return response.text.strip()                                        # החזרת הטקסט שחזר, בלי רווחים מיותרים בקצוות
    except Exception:                                                     # כל שגיאה (רשת/מכסה/מפתח שגוי וכו')
        return None                                                          # מחזירים None - הקוד הקורא יטפל בעדינות
