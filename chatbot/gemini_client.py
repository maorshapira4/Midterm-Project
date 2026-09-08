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
import time   # להמתנה קצרה בין ניסיונות חוזרים (backoff)
import traceback   # לרישום פרטי שגיאה מלאים ל-log של השרת, בלי לחשוף אותם ללקוח עצמו

import config   # קובץ ההגדרות הכלליות - כאן נמצא מפתח ה-API ושם המודל

from google import genai            # ה-SDK הרשמי של Google ל-Gemini API
from google.genai import types      # טיפוסי ההגדרות (GenerateContentConfig וכו') של ה-SDK

_client = None   # מופע יחיד (singleton) של הלקוח, כדי לא ליצור חיבור חדש בכל קריאה

# כמה פעמים לנסות שוב קריאה שנכשלה משגיאה *זמנית* בלבד (עומס על השרת של Google / חריגה רגעית).
# חשוב: ניסיון חוזר נעשה אך ורק כשהבקשה נכשלה ולא התקבלה תשובה - כלומר הוא לא "מבזבז" מכסה על
# תשובה שכבר קיבלנו. שגיאות קבועות (מפתח שגוי, מודל לא קיים, בקשה לא חוקית) לא מנוסות שוב בכלל,
# כי ניסיון נוסף בהן יחזיר בוודאות את אותה שגיאה - וזה כן היה בזבוז.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 1.0   # ההמתנה מוכפלת בכל ניסיון: שנייה, שתיים, ארבע

# מילות מפתח שמזהות שגיאה זמנית שכדאי לנסות בעקבותיה שוב
_TRANSIENT_ERROR_MARKERS = (
    "UNAVAILABLE",          # 503 - "המודל בעומס כרגע, נסו שוב מאוחר יותר"
    "RESOURCE_EXHAUSTED",     # 429 - חריגה רגעית ממספר הבקשות המותר לדקה
    "INTERNAL",                 # 500 - תקלה זמנית בצד של Google
    "DEADLINE_EXCEEDED",          # פסק זמן ברשת
    "503", "429", "500",            # קודי הסטטוס עצמם, ליתר ביטחון
)

# כיבוי ה"חשיבה" (thinking) המקדימה של המודל בשתי הקריאות שלנו. מודלי Gemini החדשים "חושבים"
# לפני שהם עונים, וזה מצוין למשימות מורכבות - אבל שתי המשימות שלנו פשוטות ומכניות: חילוץ ארבעה
# שדות מתוך משפט, וניסוח משפט אחד מתוך עובדות שכבר סופקו. במדידה בפועל כיבוי החשיבה קיצר את
# החילוץ מ-1.19 ל-0.68 שניות ואת הניסוח מ-4.18 ל-1.73 שניות - כלומר יותר מפי שניים מהר - עם
# פלט זהה לחלוטין בשני המקרים. כלומר: שיפור מהירות שלא בא על חשבון איכות התשובה.
_NO_THINKING = types.ThinkingConfig(thinking_budget=0)


def _log_error(where, error):
    """רושם שגיאה מלאה (כולל traceback) ל-stderr, כדי שתופיע ב-Error log של השרת (למשל
    PythonAnywhere) - בלי לחשוף שום פרט טכני/שגיאה ללקוח עצמו, שממשיך לקבל הודעה עדינה בלבד."""
    print(f"[chatbot/gemini_client] שגיאה ב-{where}: {error!r}", file=sys.stderr)   # שורת סיכום קצרה
    traceback.print_exc(file=sys.stderr)                                              # ה-traceback המלא


def _is_transient(error):
    """מחליט האם שגיאה היא זמנית (כדאי לנסות שוב) או קבועה (אין טעם לנסות שוב)."""
    error_text = f"{type(error).__name__} {error}"                       # שם סוג השגיאה + הטקסט שלה
    return any(marker in error_text for marker in _TRANSIENT_ERROR_MARKERS)  # האם מופיע סימן לשגיאה זמנית


def _is_quota_exhausted(error):
    """מזהה שגיאת מכסה (429). חשוב להבדיל אותה מעומס רגעי: אם המכסה *היומית* של מודל מסוים
    נגמרה, אין שום טעם לנסות שוב את אותו מודל - צריך לעבור למודל הבא בשרשרת."""
    error_text = f"{type(error).__name__} {error}"
    return "RESOURCE_EXHAUSTED" in error_text or "429" in error_text


def _call_with_retry(where, api_call):
    """מריץ קריאת API, עם שתי רמות של עמידות בפני תקלות:
      1. ניסיון חוזר על אותו מודל, אם השגיאה זמנית (עומס רגעי) - עם המתנה מתארכת.
      2. מעבר אוטומטי למודל הבא ברשימת config.GEMINI_MODELS, אם המודל הנוכחי אינו זמין או
         שהמכסה החינמית שלו נוצלה במלואה.
    מחזיר את תוצאת הקריאה מהמודל הראשון שהצליח, או None אם כל המודלים נכשלו (ואז הקוד הקורא
    מציג הודעה עדינה למשתמש/ת במקום לקרוס).

    שתי הרמות האלה נוספו אחרי שבבדיקות אמיתיות התגלו שתי תקלות: עומס זמני (503) שהשבית את
    הבוט לגמרי, ומכסה יומית קטנה מאוד במודלים החדשים (20 בקשות ליום) שהייתה משביתה את האתר
    החי אחרי כמה שיחות בלבד."""
    api_call_failed_with = None                                       # השגיאה האחרונה שנרשמה, לדיווח בסוף

    for model_name in config.GEMINI_MODELS:                           # מעבר על שרשרת המודלים, לפי סדר עדיפות
        for attempt in range(1, _MAX_RETRIES + 1):                       # ניסיון ראשון + ניסיונות חוזרים
            try:
                return api_call(model_name)                                 # הקריאה עצמה, עם המודל הנוכחי
            except Exception as error:                                     # כל שגיאה שהיא
                api_call_failed_with = error                                  # שמירת השגיאה לדיווח
                if _is_quota_exhausted(error):                                  # מכסה נוצלה - אין טעם לנסות שוב כאן
                    print(f"[chatbot/gemini_client] המכסה של המודל {model_name} נוצלה, "
                          f"עוברים למודל הבא ברשימה...", file=sys.stderr)
                    break                                                        # יציאה מלולאת הניסיונות, למודל הבא
                if attempt < _MAX_RETRIES and _is_transient(error):              # עומס זמני - כדאי לנסות שוב
                    delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))       # המתנה מתארכת: 1, 2, 4 שניות
                    print(f"[chatbot/gemini_client] שגיאה זמנית ב-{where} במודל {model_name} "
                          f"(ניסיון {attempt}), מנסה שוב בעוד {delay} שניות...", file=sys.stderr)
                    time.sleep(delay)                                             # המתנה לפני הניסיון הבא
                    continue                                                        # ניסיון נוסף באותו מודל
                break                                                          # שגיאה קבועה - מעבר למודל הבא

    _log_error(where, api_call_failed_with)   # כל המודלים נכשלו - רישום השגיאה האחרונה ל-log של השרת
    return None                                  # הקוד הקורא יטפל בעדינות ויציג הודעה ידידותית


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
    def do_call(model_name):
        """הקריאה עצמה - מקבלת את שם המודל, כדי ש-_call_with_retry יוכל לנסות מודלים שונים."""
        client = _get_client()                                          # קבלת הלקוח (או יצירתו אם עוד לא נוצר)
        response = client.models.generate_content(                        # קריאת ה-API של הפונקציה הזו
            model=model_name,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,                       # הוראת המערכת הקשיחה (מוגדרת בקובץ הקורא)
                response_mime_type="application/json",                          # מכריח תשובה בפורמט JSON בלבד
                response_schema=response_schema,                                   # מכריח את המבנה המדויק של ה-JSON
                temperature=0,                                                        # ללא "יצירתיות" - חילוץ עובדתי ועקבי
                thinking_config=_NO_THINKING,                                            # בלי "חשיבה" מקדימה - ראו הערה למעלה
            ),
        )
        return json.loads(response.text)                                    # פענוח הטקסט שחזר לאובייקט פייתון (dict)

    return _call_with_retry("extract_json", do_call)   # הרצה עם ניסיונות חוזרים ומעבר בין מודלים לפי הצורך


def generate_text(system_instruction, user_message):
    """קריאת API אחת ל-Gemini לניסוח תשובה טבעית בעברית (לא JSON) - משמשת רק לניסוח התשובה
    הסופית עם הפער בין התאריך שהלקוח/ה טען/ה לבין התאריך האמיתי, אחרי שכבר יש בידינו את כל
    הנתונים האמיתיים. מחזירה מחרוזת טקסט, או None אם הייתה שגיאה."""
    def do_call(model_name):
        """הקריאה עצמה - מקבלת את שם המודל, כדי ש-_call_with_retry יוכל לנסות מודלים שונים."""
        client = _get_client()                                          # קבלת הלקוח (או יצירתו אם עוד לא נוצר)
        response = client.models.generate_content(                        # קריאת ה-API של הפונקציה הזו
            model=model_name,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,                       # הוראת המערכת (מוגדרת בקובץ הקורא)
                temperature=0.3,                                                # מעט "טבעיות" בניסוח, אך לא מוגזם
                thinking_config=_NO_THINKING,                                      # בלי "חשיבה" מקדימה - ראו הערה למעלה
            ),
        )
        return response.text.strip()                                        # החזרת הטקסט שחזר, בלי רווחים מיותרים בקצוות

    return _call_with_retry("generate_text", do_call)   # הרצה עם ניסיונות חוזרים ומעבר בין מודלים לפי הצורך
