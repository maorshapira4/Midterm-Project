"""
app.py - נקודת הכניסה הראשית של האפליקציה.
קובץ זה בלבד אחראי על: הכנת בסיס הנתונים לפני ההרצה, יצירת אפליקציית ה-Flask, חיבור כל
מסכי המערכת (blueprints) אליה, והפעלת השרת המקומי. כדי להריץ את כל המערכת - מריצים קובץ זה בלבד.
"""

from flask import Flask, render_template   # מחלקת האפליקציה הראשית של Flask, וכלי הצגת תבניות

import config       # קובץ ההגדרות הכלליות של הפרויקט
import database      # שכבת הגישה לבסיס הנתונים, ליצירת הטבלאות
import seed_services   # סקריפט הזנת רשימת השירותים הקבועה

from routes.booking_routes import booking_bp     # מסכי הלקוח (הזמנה, ביטול)
from routes.admin_routes import admin_bp           # מסכי הניהול (תורים, לקוחות, לידים, חשבוניות)
from routes.dashboard_routes import dashboard_bp     # מסך לוח המחוונים
from routes.auth_routes import auth_bp                 # מסכי ההתחברות וההתנתקות של המנהל/ת


def create_app():
    """בונה ומחזירה את אפליקציית ה-Flask המוכנה, אחרי שכל הבלוקים (blueprints) חוברו אליה."""
    app = Flask(__name__)                    # יצירת אובייקט אפליקציית Flask חדש
    app.secret_key = config.SECRET_KEY          # הגדרת מפתח סודי, נדרש עבור הודעות flash בין דפים

    app.register_blueprint(booking_bp)            # חיבור מסכי הלקוח לאפליקציה
    app.register_blueprint(admin_bp)                 # חיבור מסכי הניהול לאפליקציה
    app.register_blueprint(dashboard_bp)               # חיבור מסך הדשבורד לאפליקציה
    app.register_blueprint(auth_bp)                      # חיבור מסכי ההתחברות וההתנתקות לאפליקציה

    @app.route("/")                                       # הגדרת עמוד הבית של האתר
    def home_page():
        """מציג עמוד פתיחה עם שתי אפשרויות: כניסה כלקוח לקביעת תור, או כניסה כמנהל/ת לניהול המערכת."""
        return render_template("home.html")                 # הצגת תבנית עמוד הבית

    return app                                                 # החזרת האפליקציה המוכנה לשימוש


database.init_db()             # יצירת כל הטבלאות בבסיס הנתונים, אם הן עדיין לא קיימות - תמיד, גם בייבוא
seed_services.seed_services()    # הזנת רשימת השירותים הקבועה, אם היא עדיין לא קיימת - תמיד, גם בייבוא
# שתי השורות למעלה רצות תמיד (לא רק תחת __main__), כדי שגם שרת אינטרנט חיצוני שרק "מייבא" את app
# (כמו PythonAnywhere, שלא מריץ python app.py בעצמו) יקבל בסיס נתונים מוכן ותקין

app = create_app()   # יצירת מופע האפליקציה ברמת המודול, כדי ש-Flask יוכל למצוא אותו בקלות בעת ההרצה

if __name__ == "__main__":         # הבלוק הזה רץ רק כאשר מריצים את הקובץ הזה ישירות (python app.py)
    print(f"האפליקציה רצה בכתובת: http://localhost:{config.SERVER_PORT}")  # הודעה ידידותית בטרמינל
    app.run(debug=True, port=config.SERVER_PORT)  # הפעלת שרת הפיתוח המקומי של Flask, עם מצב דיבוג פעיל
