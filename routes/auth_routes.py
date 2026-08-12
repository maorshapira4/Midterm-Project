"""
routes/auth_routes.py - מסך התחברות למנהל/ת: הזנת סיסמת ניהול כדי לקבל גישה למסכי הניהול,
וכן פעולת התנתקות.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session  # כלי Flask הדרושים

import config   # קובץ ההגדרות, שבו נמצאת סיסמת הניהול הנכונה

auth_bp = Blueprint("auth", __name__)   # יצירת Blueprint למסכי ההתחברות וההתנתקות (ללא תחילית נתיב מיוחדת)


@auth_bp.route("/login", methods=["GET"])
def login():
    """מציג את טופס הזנת סיסמת הניהול."""
    next_page = request.args.get("next") or url_for("dashboard.show_dashboard")  # לאן לחזור אחרי התחברות מוצלחת
    return render_template("login.html", next=next_page)   # הצגת תבנית ההתחברות, עם זכירת יעד החזרה


@auth_bp.route("/login", methods=["POST"])
def submit_login():
    """בודק את הסיסמה שהוזנה מול סיסמת הניהול מ-config.py, ואם היא נכונה - מסמן את המשתמש/ת כמחובר/ת."""
    password = request.form.get("password")                            # הסיסמה שהוזנה בטופס על ידי המשתמש/ת
    next_page = request.form.get("next") or url_for("dashboard.show_dashboard")  # לאן לחזור אחרי הצלחה
    if password == config.ADMIN_PASSWORD:                                 # השוואת הסיסמה שהוזנה לסיסמה הנכונה
        session["is_admin"] = True                                          # סימון המשתמש/ת כמחובר/ת בזיכרון הסשן
        flash("התחברת בהצלחה כמנהל/ת")                                        # הודעת הצלחה ידידותית
        return redirect(next_page)                                            # מעבר למסך שביקשו להגיע אליו במקור
    flash("סיסמה שגויה, נסו שוב")                                          # הודעת שגיאה אם הסיסמה לא נכונה
    return redirect(url_for("auth.login", next=next_page))                    # חזרה לטופס ההתחברות


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """מנתק את המנהל/ת: מוחק את דגל ההתחברות מהסשן, כך שגישה למסכי הניהול תדרוש סיסמה מחדש."""
    session.pop("is_admin", None)          # הסרת דגל ההתחברות מהסשן, אם הוא קיים בו
    flash("התנתקת בהצלחה")                     # הודעת אישור ידידותית
    return redirect(url_for("home_page"))        # חזרה לעמוד הבית של האתר
