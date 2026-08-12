"""
routes/dashboard_routes.py - מסך לוח המחוונים (Dashboard) של המנהל/ת: סיכום תורים השבוע,
תורים שהושלמו בשבוע שעבר, והכנסות שבועיות/חודשיות.
"""

from flask import Blueprint, render_template   # כלי Flask הדרושים לבניית עמוד הדשבורד

import models.reports as reports   # מודול חישובי הדשבורד וההכנסות
import auth                        # מודול ההזדהות, כדי לחסום גישה למי שלא מחובר/ת כמנהל/ת

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/admin")  # Blueprint למסך הדשבורד, תחת /admin


@dashboard_bp.before_request
def block_non_admins():
    """רץ אוטומטית לפני כל בקשה למסך הדשבורד - חוסם גישה למי שלא הזין/ה את סיסמת הניהול."""
    return auth.require_admin_login()   # מחזיר הפניה להתחברות אם לא מחובר, או None כדי להמשיך כרגיל


@dashboard_bp.route("/dashboard")
def show_dashboard():
    """מציג את לוח המחוונים: תורים השבוע, תורים שהושלמו שבוע שעבר, והכנסות שבועיות/חודשיות."""
    week_appointments = reports.get_current_week_appointments()   # שליפת רשימת התורים בשבוע הנוכחי
    last_week_completed = reports.get_last_week_completed_count()   # ספירת התורים שהושלמו בשבוע שעבר
    weekly_revenue = reports.get_weekly_revenue(0)                     # חישוב ההכנסה בשבוע הנוכחי
    monthly_revenue = reports.get_current_month_revenue()                # חישוב ההכנסה בחודש הנוכחי
    return render_template(                                               # הצגת תבנית הדשבורד עם כל הנתונים
        "admin_dashboard.html",
        week_appointments=week_appointments,
        week_appointments_count=len(week_appointments),
        last_week_completed=last_week_completed,
        weekly_revenue=weekly_revenue,
        monthly_revenue=monthly_revenue,
    )
