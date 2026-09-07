"""
routes/booking_routes.py - כל מסכי הלקוח: צפייה בתורים פנויים והזמנת תור עצמאית,
וכן צפייה בתורים הקיימים שלו וביטול תור עתידי (מוחק אותו לגמרי מבסיס הנתונים).
"""

import datetime  # מודול לעבודה עם תאריכים, כדי לדעת מהו "היום" הנוכחי

from flask import Blueprint, render_template, request, redirect, url_for, flash  # כלי Flask הדרושים

import models.appointments as appointments   # מודול הלוגיקה של תורים
import models.customers as customers         # מודול הלוגיקה של לקוחות (איתור לקוח קיים או יצירת לקוח חדש)

booking_bp = Blueprint("booking", __name__)   # יצירת Blueprint למסכי הלקוח (ללא תחילית נתיב מיוחדת)


@booking_bp.route("/book", methods=["GET"])
def show_booking_page():
    """מציג את עמוד ההזמנה: בחירת שירות אחד או יותר ותאריך, ולאחר מכן את משבצות הזמן הפנויות לבחירה."""
    service_list = appointments.list_services()              # שליפת רשימת השירותים עבור תיבות הסימון בטופס
    selected_service_ids = request.args.getlist("service_ids")  # קריאת כל השירותים שנבחרו, אם קיימים בכתובת
    selected_date = request.args.get("date")                     # קריאת התאריך שנבחר, אם קיים בכתובת

    free_slots = []                                                 # רשימת משבצות הזמן הפנויות (ריקה כברירת מחדל)
    selected_services = []                                            # פרטי השירותים שנבחרו (יישלפו רק אם צריך)
    total_duration = 0                                                  # משך כולל של השירותים שנבחרו, בדקות
    total_price = 0                                                       # מחיר כולל של השירותים שנבחרו, בשקלים
    if selected_service_ids and selected_date:                            # אם המשתמש כבר בחר שירותים ותאריך
        selected_services = appointments.get_services_by_ids(selected_service_ids)  # שליפת פרטי השירותים שנבחרו
        if selected_services:                                                 # אם נמצא לפחות שירות אחד תקין
            total_duration = sum(s["default_duration_minutes"] for s in selected_services)  # סכימת המשך הכולל
            total_price = sum(s["default_price"] for s in selected_services)                  # סכימת המחיר הכולל
            free_slots = appointments.get_free_slots(selected_date, total_duration)               # חישוב המשבצות הפנויות

    today = datetime.date.today().isoformat()      # תאריך היום הנוכחי, לשימוש כתאריך מינימלי בטופס
    return render_template(                            # הצגת תבנית עמוד ההזמנה עם כל הנתונים שנאספו
        "client_booking.html",
        services=service_list,
        selected_service_ids=[int(sid) for sid in selected_service_ids],  # המרה למספרים, לצורך השוואה בתבנית
        selected_date=selected_date,
        selected_services=selected_services,
        total_duration=total_duration,
        total_price=total_price,
        free_slots=free_slots,
        today=today,
    )


@booking_bp.route("/book", methods=["POST"])
def submit_booking():
    """קולט הזמנת תור מהלקוח (עם שירות אחד או יותר), מוסיף אותו אוטומטית לרשימת הלקוחות
    (אם הוא עדיין לא רשום), ושומר את התור עם המשך והמחיר הכוללים של כל השירותים שנבחרו."""
    service_ids = request.form.getlist("service_ids")     # רשימת מזהי השירותים שסומנו בטופס
    appointment_date = request.form.get("appointment_date")  # תאריך התור שנבחר
    appointment_time = request.form.get("appointment_time")    # שעת התור שנבחרה
    full_name = request.form.get("full_name")             # שם הלקוח מהטופס
    phone = request.form.get("phone")                       # טלפון הלקוח מהטופס
    email = request.form.get("email")                         # אימייל הלקוח מהטופס - שדה חובה
    gender = request.form.get("gender")                         # מגדר בעל/ת התור מהטופס

    if not service_ids:                                                        # אם לא סומן אף שירות בטופס
        flash("נא לבחור לפחות שירות אחד")                                          # הודעת שגיאה למשתמש
        return redirect(url_for("booking.show_booking_page"))                        # חזרה לעמוד ההזמנה

    if not email:                                                              # אימייל הוא שדה חובה - הוא אמצעי האימות
        flash("חובה להזין כתובת אימייל - היא משמשת לאימות הזהות שלך במערכת")
        return redirect(url_for("booking.show_booking_page"))

    customer_id = None                                                       # מזהה הלקוח שישויך לתור (ריק כברירת מחדל)
    if full_name and phone:                                                    # רק אם הוזנו שם וטלפון תקינים
        existing_customer = customers.find_customer_by_email(email)              # חיפוש לקוח קיים לפי האימייל שהוזן
        if existing_customer:                                                      # אם האימייל כבר רשום - זהו אותו לקוח
            customer_id = existing_customer["id"]                                    # שימוש ברשומת הלקוח הקיימת
        else:                                                                      # אימייל חדש - יוצרים לקוח/ה חדש/ה
            try:
                customer_id = customers.add_customer(full_name=full_name, phone=phone, email=email)
            except ValueError as error:                                              # אימייל לא תקין או כפול
                flash(str(error))
                return redirect(url_for("booking.show_booking_page"))

    try:
        appointments.add_appointment(                                  # ניסיון להוסיף את התור לבסיס הנתונים
            customer_id=customer_id,
            guest_name=full_name,
            guest_phone=phone,
            gender=gender,
            service_ids=[int(sid) for sid in service_ids],                # המרת מזהי השירותים למספרים שלמים
            appointment_date=appointment_date,
            appointment_time=appointment_time,
        )
        flash("התור נקבע בהצלחה! ניתן לצפות בו ולבטל אותו במסך 'התורים שלי'")   # הודעת הצלחה ידידותית
        return redirect(url_for("booking.my_appointments", phone=phone))          # מעבר למסך "התורים שלי"
    except ValueError as error:                                            # תפיסת שגיאת ולידציה/התנגשות
        flash(str(error))                                                    # הצגת הודעת השגיאה המדויקת
        return redirect(url_for(                                              # חזרה לעמוד ההזמנה עם אותה בחירה
            "booking.show_booking_page", service_ids=service_ids, date=appointment_date
        ))


@booking_bp.route("/my-appointments", methods=["GET"])
def my_appointments():
    """מציג ללקוח את כל התורים העתידיים הרשומים על שם מספר הטלפון שהזין, עם אפשרות ביטול."""
    phone = request.args.get("phone")                            # קריאת מספר הטלפון שהוזן על ידי הלקוח
    upcoming = []                                                   # רשימת התורים העתידיים (ריקה כברירת מחדל)
    if phone:                                                         # אם הלקוח הזין מספר טלפון
        today = datetime.date.today().isoformat()                       # תאריך היום, לסינון תורים עתידיים בלבד
        matching_customer = customers.find_customer_by_phone(phone)       # חיפוש לקוח קיים עם אותו מספר טלפון
        all_appointments = appointments.list_appointments()                 # שליפת כל התורים במערכת
        for appt in all_appointments:                                         # מעבר על כל תור במערכת
            belongs_to_phone = appt["guest_phone"] == phone or (                # התור שייך לטלפון הזה אם...
                matching_customer and appt["customer_id"] == matching_customer["id"]  # ...או שהוא שייך ללקוח שנמצא
            )
            is_upcoming_and_pending = appt["appointment_date"] >= today and appt["status"] == "ממתין"  # ועתידי וממתין
            if belongs_to_phone and is_upcoming_and_pending:                       # אם שני התנאים מתקיימים
                upcoming.append(appt)                                                # הוספת התור לרשימה הסופית
    return render_template("my_appointments.html", appointments=upcoming, phone=phone)  # הצגת התבנית


@booking_bp.route("/cancel/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    """מבטל תור עתידי של הלקוח - מוחק אותו לחלוטין מבסיס הנתונים, כפי שנדרש בדרישות."""
    phone = request.form.get("phone")                     # מספר הטלפון, כדי לחזור לאותו מסך אחרי הביטול
    appointments.delete_appointment(appointment_id)          # מחיקת התור מבסיס הנתונים
    flash("התור בוטל ונמחק בהצלחה")                             # הודעת הצלחה ללקוח
    return redirect(url_for("booking.my_appointments", phone=phone))  # חזרה למסך "התורים שלי"
