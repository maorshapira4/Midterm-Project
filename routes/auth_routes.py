"""
routes/auth_routes.py - מסך התחברות למנהל/ת: הזנת סיסמת ניהול כדי לקבל גישה למסכי הניהול,
וכן פעולת התנתקות.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session  # כלי Flask הדרושים

import config   # קובץ ההגדרות, שבו נמצאת סיסמת הניהול הנכונה

auth_bp = Blueprint("auth", __name__)   # יצירת Blueprint למסכי ההתחברות וההתנתקות (ללא תחילית נתיב מיוחדת)


def _safe_next_page(candidate):
    """מוודא שיעד ה'next' (לאן לחזור אחרי התחברות) הוא נתיב פנימי בטוח באתר שלנו בלבד, ולא כתובת
    חיצונית. בלי הבדיקה הזו, מישהו זדוני היה יכול לשלוח קישור כמו /login?next=https://אתר-מזויף.com
    כדי שאחרי הזנת הסיסמה הנכונה, המנהל/ת יופנה/תופנה בלי לשים לב לאתר חיצוני (למשל אתר פישינג
    שמחקה את מסך ההתחברות שוב, כדי לגנוב את הסיסמה בפעם השנייה)."""
    if candidate and candidate.startswith("/") and not candidate.startswith("//") and not candidate.startswith("/\\"):
        return candidate   # נתיב יחסי פנימי תקין - מותר להשתמש בו
    return url_for("dashboard.show_dashboard")   # בכל מקרה אחר (כתובת חיצונית/ריק/חשוד) - יעד ברירת מחדל בטוח


@auth_bp.route("/login", methods=["GET"])
def login():
    """מציג את טופס הזנת סיסמת הניהול."""
    next_page = _safe_next_page(request.args.get("next"))  # לאן לחזור אחרי התחברות מוצלחת, רק אם זה נתיב פנימי בטוח
    return render_template("login.html", next=next_page)   # הצגת תבנית ההתחברות, עם זכירת יעד החזרה


@auth_bp.route("/login", methods=["POST"])
def submit_login():
    """בודק את הסיסמה שהוזנה מול סיסמת הניהול מ-config.py, ואם היא נכונה - מסמן את המשתמש/ת כמחובר/ת."""
    password = request.form.get("password")                            # הסיסמה שהוזנה בטופס על ידי המשתמש/ת
    next_page = _safe_next_page(request.form.get("next"))                 # לאן לחזור אחרי הצלחה, רק יעד פנימי בטוח
    if password == config.ADMIN_PASSWORD:                                 # השוואת הסיסמה שהוזנה לסיסמה הנכונה
        session["is_admin"] = True                                          # סימון המשתמש/ת כמחובר/ת בזיכרון הסשן
        session.permanent = True   # מסמן את הסשן כ"בעל תוקף מוגבל" - כדי ש-PERMANENT_SESSION_LIFETIME
        # (תפוגת חוסר פעילות שהוגדרה ב-app.py) באמת תיכנס לתוקף. בלי השורה הזו הסשן נשאר תקף לנצח
        # (עד שסוגרים את הדפדפן), גם אם הגדרנו זמן תפוגה - זה בדיוק הבאג שגרם לכניסת המנהל/ת "להישאר דלוקה".
        flash("התחברת בהצלחה כמנהל/ת")                                        # הודעת הצלחה ידידותית
        return redirect(next_page)                                            # מעבר למסך שביקשו להגיע אליו במקור
    flash("סיסמה שגויה, נסו שוב")                                          # הודעת שגיאה אם הסיסמה לא נכונה
    return redirect(url_for("auth.login", next=next_page))                    # חזרה לטופס ההתחברות


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """מנתק את המנהל/ת: מוחק את כל תוכן הסשן (לא רק את דגל is_admin), כך שגישה למסכי הניהול
    תדרוש סיסמה מחדש, ולא יישארו בטעות שאריות מידע מהתחברות קודמת."""
    session.clear()                          # ניקוי מלא של כל הסשן, לא רק הדגל הבודד - התנתקות אמיתית ומלאה
    flash("התנתקת בהצלחה")                     # הודעת אישור ידידותית
    return redirect(url_for("home_page"))        # חזרה לעמוד הבית של האתר
