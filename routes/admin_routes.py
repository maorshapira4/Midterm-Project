"""
routes/admin_routes.py - כל מסכי הניהול של המנהל/ת: ניהול תורים, ניהול לקוחות, ניהול לידים
והפקת חשבוניות. כל route כאן מייצג עמוד או פעולה אחת שהמנהל/ת מבצע/ת במערכת.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash  # כלי Flask הדרושים לבניית עמודים וטפסים

import models.appointments as appointments   # מודול הלוגיקה של תורים
import models.customers as customers         # מודול הלוגיקה של לקוחות
import models.invoices as invoices           # מודול הלוגיקה של חשבוניות
import models.leads as leads                 # מודול הלוגיקה של לידים
import auth                                  # מודול ההזדהות, כדי לחסום גישה למי שלא מחובר/ת כמנהל/ת

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")  # יצירת Blueprint לכל מסכי הניהול, תחת הנתיב /admin


@admin_bp.before_request
def block_non_admins():
    """רץ אוטומטית לפני כל בקשה למסכי הניהול (תורים/לקוחות/לידים/חשבוניות) - חוסם גישה למי
    שלא הזין/ה את סיסמת הניהול, ומפנה אותו/ה למסך ההתחברות."""
    return auth.require_admin_login()   # מחזיר הפניה להתחברות אם לא מחובר, או None כדי להמשיך כרגיל


# ----------------------- ניהול תורים -----------------------

@admin_bp.route("/appointments")
def list_appointments():
    """מציג את רשימת כל התורים במערכת, עם אפשרות סינון לפי סטטוס ולפי תאריך (query params)."""
    status_filter = request.args.get("status") or None            # קריאת פרמטר הסינון "status" מהכתובת, אם קיים
    date_filter = request.args.get("date") or None                  # קריאת פרמטר הסינון "date" מהכתובת, אם קיים
    appointment_list = appointments.list_appointments(status_filter, date_filter)  # שליפת רשימת התורים המסוננת
    for appointment in appointment_list:                              # סימון לכל תור האם מועדו כבר עבר,
        appointment["is_past"] = appointments.is_past(appointment)       # כדי שהתבנית תדע אם להציג "בוצע"
    service_list = appointments.list_services()                       # שליפת רשימת השירותים עבור תפריט הבחירה בטופס
    customer_list = customers.list_customers()                          # שליפת רשימת הלקוחות עבור תפריט הקישור בטופס
    return render_template(                                              # הצגת תבנית ה-HTML עם כל הנתונים שאספנו
        "admin_appointments.html",
        appointments=appointment_list,
        services=service_list,
        customers=customer_list,
        status_filter=status_filter,
        date_filter=date_filter,
    )


@admin_bp.route("/appointments/add", methods=["POST"])
def add_appointment():
    """מוסיף תור חדש מתוך טופס הניהול, עם שירות אחד או יותר (מיועד למקרה שהמנהל/ת קובע/ת תור
    עבור לקוח, למשל בטלפון)."""
    customer_id = request.form.get("customer_id") or None                 # מזהה לקוח קיים, אם נבחר מהרשימה
    guest_name = request.form.get("guest_name") or None                    # שם, במקרה שאין לקוח קיים נבחר
    guest_phone = request.form.get("guest_phone") or None                    # טלפון, במקרה שאין לקוח קיים נבחר
    gender = request.form.get("gender")                                       # מגדר בעל/ת התור
    service_ids = request.form.getlist("service_ids")                           # רשימת מזהי השירותים שסומנו בטופס
    appointment_date = request.form.get("appointment_date")                       # תאריך התור מהטופס
    appointment_time = request.form.get("appointment_time")                         # שעת התור מהטופס

    if not service_ids:                                                                # אם לא סומן אף שירות בטופס
        flash("נא לבחור לפחות שירות אחד")                                                # הודעת שגיאה למשתמש
        return redirect(url_for("admin.list_appointments"))                                # חזרה לעמוד התורים

    if not customer_id and guest_phone:                                                # אם לא נבחר לקוח קיים ידנית
        existing = customers.find_customer_by_phone(guest_phone)                          # ניסיון לאתר לקוח לפי הטלפון
        if existing:                                                                        # אם נמצא לקוח מתאים
            customer_id = existing["id"]                                                      # קישור אוטומטי ללקוח הקיים

    try:
        appointments.add_appointment(                                    # ניסיון להוסיף את התור לבסיס הנתונים
            customer_id=int(customer_id) if customer_id else None,
            guest_name=guest_name,
            guest_phone=guest_phone,
            gender=gender,
            service_ids=[int(sid) for sid in service_ids],                  # המרת מזהי השירותים למספרים שלמים
            appointment_date=appointment_date,
            appointment_time=appointment_time,
        )
        flash("התור נוסף בהצלחה")                                            # הודעת הצלחה למשתמש
    except ValueError as error:                                                # תפיסת שגיאות ולידציה/התנגשות
        flash(str(error))                                                        # הצגת הודעת השגיאה המדויקת למשתמש
    return redirect(url_for("admin.list_appointments"))                            # חזרה לעמוד רשימת התורים


@admin_bp.route("/appointments/<int:appointment_id>/status", methods=["POST"])
def update_appointment_status(appointment_id):
    """מעדכן את הסטטוס של תור קיים. שימו לב: 'בוטל' מוחק את התור מהמערכת במקום לשמור אותו
    עם סטטוס 'בוטל', ו'בוצע' מותר רק לתור שמועדו כבר עבר - שני הכללים נאכפים במודל."""
    new_status = request.form.get("status")                       # קריאת הסטטוס החדש מהטופס
    return_to = request.form.get("return_to")                       # לאן לחזור אחרי הפעולה (רשימה או מסך הסקירה)
    try:
        result = appointments.update_status(appointment_id, new_status)  # ניסיון לעדכן/למחוק
        flash("התור בוטל והוסר מהרשימה" if result == "deleted" else "סטטוס התור עודכן")
    except ValueError as error:                                       # תפיסת שגיאת סטטוס לא חוקי או תור עתידי
        flash(str(error))                                                # הצגת השגיאה המדויקת למשתמש
    if return_to == "review":                                          # אם הגענו ממסך סקירת התורים שעברו
        return redirect(url_for("admin.review_past_appointments"))        # חוזרים אליו, להמשך הסקירה
    return redirect(url_for("admin.list_appointments"))                   # אחרת - חזרה לעמוד רשימת התורים


@admin_bp.route("/appointments/review")
def review_past_appointments():
    """מסך סקירה: מציג את כל התורים שמועדם עבר אך עדיין מסומנים 'ממתין', ומבקש מהמנהל/ת
    להחליט לגבי כל אחד - האם הוא בוצע, או שהוא מבוטל (ואז יימחק). המסך הזה מוקפץ אוטומטית
    מיד אחרי כניסה לניהול, כשיש תורים כאלה שממתינים להחלטה."""
    pending = appointments.list_past_pending_appointments()      # שליפת התורים שמועדם עבר וטרם הוכרעו
    if not pending:                                                 # אם אין כאלה - אין מה לסקור
        flash("אין תורים שעבר זמנם וממתינים להחלטה")
        return redirect(url_for("admin.list_appointments"))
    return render_template("admin_review_past.html", appointments=pending)   # הצגת מסך הסקירה


@admin_bp.route("/appointments/<int:appointment_id>/delete", methods=["POST"])
def delete_appointment(appointment_id):
    """מוחק תור קיים לחלוטין מבסיס הנתונים, לפי בקשת המנהל/ת."""
    appointments.delete_appointment(appointment_id)     # מחיקת התור מבסיס הנתונים
    flash("התור נמחק")                                     # הודעת הצלחה למשתמש
    return redirect(url_for("admin.list_appointments"))     # חזרה לעמוד רשימת התורים


# ----------------------- ניהול לקוחות -----------------------

@admin_bp.route("/customers")
def list_customers():
    """מציג את רשימת כל הלקוחות הרשומים במערכת, עם טופס להוספת לקוח חדש."""
    customer_list = customers.list_customers()      # שליפת רשימת כל הלקוחות
    return render_template("admin_customers.html", customers=customer_list)  # הצגת התבנית עם הרשימה


@admin_bp.route("/customers/add", methods=["POST"])
def add_customer():
    """מוסיף לקוח חדש לבסיס הנתונים מתוך טופס הניהול. אימייל הוא שדה חובה - הוא משמש לאימות."""
    full_name = request.form.get("full_name")     # שם הלקוח מהטופס
    phone = request.form.get("phone")               # טלפון הלקוח מהטופס
    email = request.form.get("email")                 # אימייל הלקוח מהטופס (חובה)
    try:
        customers.add_customer(full_name, phone, email)  # ניסיון להוסיף את הלקוח
        flash("הלקוח נוסף בהצלחה")                          # הודעת הצלחה
    except ValueError as error:                             # תפיסת שגיאת ולידציה (אימייל חסר/כפול/לא תקין)
        flash(str(error))                                      # הצגת השגיאה למשתמש
    return redirect(url_for("admin.list_customers"))             # חזרה לעמוד רשימת הלקוחות


@admin_bp.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    """מציג עמוד פרטי לקוח בודד: פרטיו, היסטוריית התורים שלו, וההיסטוריית חשבוניות שלו."""
    customer = customers.get_customer(customer_id)               # שליפת פרטי הלקוח
    if not customer:                                                # אם הלקוח לא נמצא
        flash("הלקוח לא נמצא")                                        # הודעת שגיאה
        return redirect(url_for("admin.list_customers"))               # חזרה לרשימת הלקוחות
    customer_appointments = customers.get_customer_appointments(customer_id)  # שליפת היסטוריית התורים של הלקוח
    customer_invoices = invoices.list_invoices_for_customer(customer_id)        # שליפת היסטוריית החשבוניות של הלקוח
    return render_template(                                          # הצגת תבנית פרטי הלקוח עם כל הנתונים
        "admin_customer_detail.html",
        customer=customer,
        appointments=customer_appointments,
        invoices=customer_invoices,
    )


@admin_bp.route("/customers/<int:customer_id>/delete", methods=["POST"])
def delete_customer(customer_id):
    """מוחק לקוח קיים מבסיס הנתונים, לפי בקשת המנהל/ת."""
    try:
        customers.delete_customer(customer_id)   # ניסיון למחוק את הלקוח מבסיס הנתונים
        flash("הלקוח נמחק")                          # הודעת הצלחה
    except ValueError as error:                        # תפיסת שגיאה אם ללקוח יש תורים/חשבוניות קיימות
        flash(str(error))                                 # הצגת הודעת השגיאה המדויקת למשתמש
    return redirect(url_for("admin.list_customers"))  # חזרה לעמוד רשימת הלקוחות


# ----------------------- חשבוניות -----------------------

@admin_bp.route("/customers/<int:customer_id>/invoice", methods=["POST"])
def create_invoice(customer_id):
    """יוצר חשבונית חדשה עבור הלקוח, ומציג אותה מיד לאחר היצירה."""
    appointment_id = request.form.get("appointment_id") or None      # מזהה תור משויך, אם נבחר
    amount = request.form.get("amount")                                 # סכום החשבונית מהטופס
    try:
        invoice_id = invoices.create_invoice(                             # ניסיון ליצור את החשבונית
            customer_id=customer_id,
            appointment_id=int(appointment_id) if appointment_id else None,
            amount=float(amount),
        )
        return redirect(url_for("admin.view_invoice", invoice_id=invoice_id))  # מעבר לעמוד הצגת החשבונית שנוצרה
    except ValueError as error:                                             # תפיסת שגיאת ולידציה
        flash(str(error))                                                     # הצגת השגיאה למשתמש
        return redirect(url_for("admin.customer_detail", customer_id=customer_id))  # חזרה לעמוד פרטי הלקוח


@admin_bp.route("/invoices/<int:invoice_id>")
def view_invoice(invoice_id):
    """מציג חשבונית בודדת בתצוגה נאה, כמו קבלה שאפשר להדפיס."""
    invoice = invoices.get_invoice(invoice_id)     # שליפת פרטי החשבונית
    if not invoice:                                   # אם החשבונית לא נמצאה
        flash("החשבונית לא נמצאה")                       # הודעת שגיאה
        return redirect(url_for("admin.list_customers"))    # חזרה לרשימת הלקוחות
    return render_template("admin_invoice.html", invoice=invoice)  # הצגת תבנית החשבונית


# ----------------------- ניהול לידים -----------------------

@admin_bp.route("/leads")
def list_leads():
    """מציג את רשימת כל הלידים במערכת, עם טופס להוספת ליד חדש."""
    lead_list = leads.list_leads()                                # שליפת רשימת כל הלידים
    return render_template("admin_leads.html", leads=lead_list, statuses=leads.LEAD_STATUSES)  # הצגת התבנית


@admin_bp.route("/leads/add", methods=["POST"])
def add_lead():
    """מוסיף ליד חדש לבסיס הנתונים מתוך טופס הניהול."""
    full_name = request.form.get("full_name")       # שם הליד מהטופס
    phone = request.form.get("phone")                 # טלפון הליד מהטופס
    source = request.form.get("source") or None          # מקור הפנייה מהטופס (אופציונלי)
    notes = request.form.get("notes") or None                # הערות מהטופס (אופציונלי)
    try:
        leads.add_lead(full_name, phone, source, notes)   # ניסיון להוסיף את הליד
        flash("הליד נוסף בהצלחה")                             # הודעת הצלחה
    except ValueError as error:                                # תפיסת שגיאת ולידציה
        flash(str(error))                                        # הצגת השגיאה למשתמש
    return redirect(url_for("admin.list_leads"))                   # חזרה לעמוד רשימת הלידים


@admin_bp.route("/leads/<int:lead_id>/status", methods=["POST"])
def update_lead_status(lead_id):
    """מעדכן את הסטטוס של ליד קיים לפי בחירת המנהל/ת."""
    new_status = request.form.get("status")       # קריאת הסטטוס החדש מהטופס
    try:
        leads.update_lead_status(lead_id, new_status)   # ניסיון לעדכן את סטטוס הליד
        flash("סטטוס הליד עודכן")                          # הודעת הצלחה
    except ValueError as error:                              # תפיסת שגיאת סטטוס לא חוקי
        flash(str(error))                                       # הצגת השגיאה למשתמש
    return redirect(url_for("admin.list_leads"))                  # חזרה לעמוד רשימת הלידים


@admin_bp.route("/leads/<int:lead_id>/convert", methods=["POST"])
def convert_lead(lead_id):
    """הופך ליד קיים ללקוח רשום, ומעביר את המנהל/ת לעמוד פרטי הלקוח החדש."""
    try:
        new_customer_id = leads.convert_lead_to_customer(lead_id)  # ניסיון להמיר את הליד ללקוח
        flash("הליד הומר ללקוח בהצלחה")                                # הודעת הצלחה
        return redirect(url_for("admin.customer_detail", customer_id=new_customer_id))  # מעבר לעמוד הלקוח החדש
    except ValueError as error:                                          # תפיסת שגיאת המרה
        flash(str(error))                                                  # הצגת השגיאה למשתמש
        return redirect(url_for("admin.list_leads"))                         # חזרה לעמוד רשימת הלידים
