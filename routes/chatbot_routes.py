"""
routes/chatbot_routes.py - מסך הצ'אטבוט ללקוחות: זיהוי לפי שיחה חופשית, אימות תעודת זהות,
ותשובה מבוססת דאטה אמיתי מהמערכת. זמין לכל לקוח/ה בלי שום צורך בהתחברות ניהול - זה בוט
הלקוחות, לא כלי ניהול. מצב השיחה (מי המועמד/ת, כמה ניסיונות אימות נותרו וכו') נשמר בין הודעה
להודעה בתוך ה-session של הדפדפן (Flask session), כדי שכל לקוח/ה ינהל/תנהל שיחה נפרדת משלו/ה.
"""

from flask import Blueprint, render_template, request, redirect, url_for, session   # כלי Flask הדרושים

import chatbot.conversation as conversation   # מוח הצ'אטבוט - מכונת המצבים של השיחה

chatbot_bp = Blueprint("chatbot", __name__)   # יצירת Blueprint למסך הצ'אטבוט (ללא תחילית נתיב מיוחדת)

_SESSION_KEY = "chatbot_state"   # המפתח שתחתיו שומרים את מצב השיחה בתוך session של Flask


def _get_or_create_state():
    """שולף את מצב השיחה הנוכחי מה-session, או יוצר מצב חדש וריק אם זו שיחה שעדיין לא התחילה."""
    if _SESSION_KEY not in session:                                   # אם עדיין אין מצב שיחה שמור עבור הדפדפן הזה
        session[_SESSION_KEY] = conversation.new_state()                # יצירת מצב שיחה חדש וריק
    return session[_SESSION_KEY]                                       # החזרת מצב השיחה (הקיים או שנוצר הרגע)


def _save_state(state):
    """שומר את מצב השיחה המעודכן בחזרה ל-session, ומסמן ל-Flask שה-session השתנה בפועל.
    זה נדרש כי Flask לא תמיד מזהה לבד שינוי בתוך dict מקונן בתוך ה-session."""
    session[_SESSION_KEY] = state                                     # שמירת המצב המעודכן
    session.modified = True                                            # סימון מפורש שה-session השתנה, לשמירה אמינה


@chatbot_bp.route("/chatbot", methods=["GET"])
def show_chatbot_page():
    """מציג את מסך הצ'אט: תמלול השיחה עד כה, וטופס פשוט לשליחת הודעה חדשה."""
    state = _get_or_create_state()                                # שליפת מצב השיחה הנוכחי (או יצירת חדש)
    return render_template("chatbot.html", history=state["history"])  # הצגת התבנית עם תמלול השיחה עד כה


@chatbot_bp.route("/chatbot/send", methods=["POST"])
def send_message():
    """מקבל הודעה חדשה מהלקוח/ה, מעביר אותה למוח הצ'אטבוט, ושומר את המצב המעודכן ב-session."""
    user_message = request.form.get("message", "")               # ההודעה החדשה שהוקלדה בטופס
    state = _get_or_create_state()                                  # שליפת מצב השיחה הנוכחי
    updated_state, _bot_reply = conversation.handle_message(state, user_message)  # עיבוד ההודעה במוח הצ'אטבוט
    _save_state(updated_state)                                        # שמירת המצב המעודכן בחזרה ל-session
    return redirect(url_for("chatbot.show_chatbot_page"))               # חזרה למסך הצ'אט, שיציג את התמלול המעודכן


@chatbot_bp.route("/chatbot/reset", methods=["POST"])
def reset_conversation():
    """מתחיל שיחה חדשה לגמרי: מוחק את מצב השיחה הקודם (כולל כל מידע על מועמד/ת ותמלול קודם)."""
    _save_state(conversation.new_state())                             # החלפת המצב במצב חדש וריק לגמרי
    return redirect(url_for("chatbot.show_chatbot_page"))               # חזרה למסך הצ'אט הריק
