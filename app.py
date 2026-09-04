"""
app.py - נקודת הכניסה הראשית של האפליקציה.
קובץ זה בלבד אחראי על: הכנת בסיס הנתונים לפני ההרצה, יצירת אפליקציית ה-Flask, חיבור כל
מסכי המערכת (blueprints) אליה, והפעלת השרת המקומי. כדי להריץ את כל המערכת - מריצים קובץ זה בלבד.
"""

from datetime import timedelta               # לחישוב משך תפוגת סשן המנהל/ת (חוסר פעילות)

from dotenv import load_dotenv   # טעינת משתני סביבה מקובץ .env מקומי (כמו GEMINI_API_KEY)
load_dotenv()                       # חייב לרוץ *לפני* import config, כי config קורא את המשתנים האלה מיד בזמן הייבוא.
# הערה: אם קובץ .env לא קיים בכלל (למשל על שרת שבו הוגדר משתנה סביבה אמיתי, כמו PythonAnywhere) -
# הפונקציה הזו פשוט לא עושה כלום ולא זורקת שגיאה, אז זה בטוח להשאיר אותה תמיד.

from flask import Flask, render_template   # מחלקת האפליקציה הראשית של Flask, וכלי הצגת תבניות

import config       # קובץ ההגדרות הכלליות של הפרויקט
import database      # שכבת הגישה לבסיס הנתונים, ליצירת הטבלאות
import seed_services   # סקריפט הזנת רשימת השירותים הקבועה
import seed_demo_data  # סקריפט הזנת דאטת דמו ללקוחות/תורים/לידים, לצורך הדגמת הצ'אטבוט

from routes.booking_routes import booking_bp     # מסכי הלקוח (הזמנה, ביטול)
from routes.admin_routes import admin_bp           # מסכי הניהול (תורים, לקוחות, לידים, חשבוניות)
from routes.dashboard_routes import dashboard_bp     # מסך לוח המחוונים
from routes.auth_routes import auth_bp                 # מסכי ההתחברות וההתנתקות של המנהל/ת
from routes.chatbot_routes import chatbot_bp             # מסך הצ'אטבוט ללקוחות (פרויקט הסיום)


def create_app():
    """בונה ומחזירה את אפליקציית ה-Flask המוכנה, אחרי שכל הבלוקים (blueprints) חוברו אליה."""
    app = Flask(__name__)                    # יצירת אובייקט אפליקציית Flask חדש
    app.secret_key = config.SECRET_KEY          # הגדרת מפתח סודי, נדרש עבור הודעות flash בין דפים ולחתימת הסשן

    # תפוגת סשן אוטומטית למנהל/ת: אחרי X דקות בלי אף בקשה חדשה, ה-session נחשב פג ודורש התחברות מחדש.
    # "SESSION_REFRESH_EACH_REQUEST" (ברירת מחדל True ב-Flask) דואג שכל בקשה חדשה "מאריכה" את השעון -
    # כך שזו תפוגה לפי חוסר פעילות, ולא זמן קבוע מרגע ההתחברות.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=config.ADMIN_SESSION_TIMEOUT_MINUTES)

    app.register_blueprint(booking_bp)            # חיבור מסכי הלקוח לאפליקציה
    app.register_blueprint(admin_bp)                 # חיבור מסכי הניהול לאפליקציה
    app.register_blueprint(dashboard_bp)               # חיבור מסך הדשבורד לאפליקציה
    app.register_blueprint(auth_bp)                      # חיבור מסכי ההתחברות וההתנתקות לאפליקציה
    app.register_blueprint(chatbot_bp)                     # חיבור מסך הצ'אטבוט ללקוחות (פרויקט הסיום)

    @app.route("/")                                       # הגדרת עמוד הבית של האתר
    def home_page():
        """מציג עמוד פתיחה עם שתי אפשרויות: כניסה כלקוח לקביעת תור, או כניסה כמנהל/ת לניהול המערכת."""
        return render_template("home.html")                 # הצגת תבנית עמוד הבית

    @app.after_request
    def add_no_cache_headers(response):
        """מוסיף לכל תגובה כותרות שאומרות לדפדפן לא לשמור אף עמוד בזיכרון המטמון (cache) או ב-
        bfcache (זיכרון "קדימה/אחורה" של הדפדפן). זה קריטי מבחינת אבטחה: בלי הכותרות האלה, אחרי
        שמנהל/ת מתנתק/ת, לחיצה על "אחורה" בדפדפן (או טעינה מחדש במקרים מסוימים) עלולה להציג עמוד
        ישן ששמור בזיכרון הדפדפן - עם תפריט הניהול המלא - בלי לפנות בכלל לשרת ולבדוק שה-session
        עדיין בתוקף. הכותרות האלה מבטיחות שכל טעינת עמוד תמיד תבדוק מול השרת מחדש."""
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"     # תמיכה בדפדפנים/פרוקסי ישנים יותר
        return response                               # החזרת התגובה עם הכותרות החדשות

    return app                                                 # החזרת האפליקציה המוכנה לשימוש


database.init_db()             # יצירת כל הטבלאות בבסיס הנתונים, אם הן עדיין לא קיימות - תמיד, גם בייבוא
seed_services.seed_services()    # הזנת רשימת השירותים הקבועה, אם היא עדיין לא קיימת - תמיד, גם בייבוא
seed_demo_data.seed_demo_data()   # הזנת דאטת דמו (לקוחות/תורים/לידים) לצורך בדיקת הצ'אטבוט, אם היא עדיין לא קיימת
# שלוש השורות למעלה רצות תמיד (לא רק תחת __main__), כדי שגם שרת אינטרנט חיצוני שרק "מייבא" את app
# (כמו PythonAnywhere, שלא מריץ python app.py בעצמו) יקבל בסיס נתונים מוכן ותקין

app = create_app()   # יצירת מופע האפליקציה ברמת המודול, כדי ש-Flask יוכל למצוא אותו בקלות בעת ההרצה

if __name__ == "__main__":         # הבלוק הזה רץ רק כאשר מריצים את הקובץ הזה ישירות (python app.py)
    print(f"האפליקציה רצה בכתובת: http://localhost:{config.SERVER_PORT}")  # הודעה ידידותית בטרמינל
    app.run(debug=True, port=config.SERVER_PORT)  # הפעלת שרת הפיתוח המקומי של Flask, עם מצב דיבוג פעיל
