"""
models/appointments.py - כל הלוגיקה שקשורה לתורים: הוספה, רשימה, עדכון סטטוס, מחיקה/ביטול,
בדיקת התנגשויות בין תורים, וחישוב אילו משבצות זמן פנויות ביום נתון.
זהו הקובץ המרכזי ביותר במערכת - הוא מממש את דרישת ה"ליבה" של הפרויקט.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path כדי לאפשר "import database" ו-"import config"

import database  # קובץ הגישה לבסיס הנתונים
import config    # קובץ ההגדרות הכלליות (שעות פעילות, אורך סלוט וכו')


def _time_to_minutes(time_str):
    """ממיר מחרוזת שעה בפורמט 'HH:MM' למספר דקות מתחילת היום (למשל '09:30' -> 570)."""
    hours, minutes = time_str.split(":")   # פיצול המחרוזת לשעות ולדקות לפי הנקודתיים
    return int(hours) * 60 + int(minutes)    # חישוב סך הדקות מתחילת היום


def _minutes_to_time(total_minutes):
    """ממיר מספר דקות מתחילת היום בחזרה למחרוזת שעה בפורמט 'HH:MM'."""
    hours = total_minutes // 60             # חישוב מספר השעות השלמות
    minutes = total_minutes % 60             # חישוב יתרת הדקות
    return f"{hours:02d}:{minutes:02d}"        # בניית מחרוזת עם אפסים מובילים אם צריך (למשל '09:05')


def has_conflict(appointment_date, appointment_time, duration_minutes, exclude_id=None):
    """בודק האם קיים תור אחר שחופף בזמן לתור המבוקש, באותו תאריך. מחזיר True אם יש התנגשות."""
    new_start = _time_to_minutes(appointment_time)          # תחילת התור החדש, בדקות מתחילת היום
    new_end = new_start + duration_minutes                    # סוף התור החדש, בדקות מתחילת היום

    connection = database.get_connection()                     # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(                                   # שליפת כל התורים הפעילים (לא בוטלו) באותו תאריך
        "SELECT id, appointment_time, duration_minutes FROM appointments "
        "WHERE appointment_date = ? AND status != 'בוטל'",
        (appointment_date,)
    ).fetchall()
    connection.close()                                             # סגירת החיבור מיד לאחר השליפה

    for row in rows:                                                 # מעבר על כל תור קיים באותו יום
        if exclude_id is not None and row["id"] == exclude_id:         # דילוג על התור עצמו (רלוונטי בעת עדכון)
            continue
        existing_start = _time_to_minutes(row["appointment_time"])       # תחילת התור הקיים, בדקות
        existing_end = existing_start + row["duration_minutes"]            # סוף התור הקיים, בדקות
        if new_start < existing_end and existing_start < new_end:            # תנאי חפיפה בין שני טווחי זמן
            return True                                                        # נמצאה התנגשות
    return False                                                                 # לא נמצאה אף התנגשות


def add_appointment(customer_id, guest_name, guest_phone, gender, service_ids,
                     appointment_date, appointment_time):
    """מוסיף תור חדש עם שירות אחד או יותר. מחשב אוטומטית את המשך והמחיר הכוללים מסכום השירותים
    שנבחרו, בודק תקינות קלט והתנגשויות, ומחזיר את מזהה התור החדש."""
    if not appointment_date or not appointment_time:            # ולידציה: לא מאפשרים תור בלי תאריך או בלי שעה
        raise ValueError("חובה לספק תאריך ושעה לתור")             # שגיאה ברורה שתוצג למשתמש
    if not gender or not service_ids:                             # ולידציה: מגדר ולפחות שירות אחד הם שדות חובה
        raise ValueError("חובה לבחור מגדר ולפחות שירות אחד עבור התור")  # שגיאה ברורה שתוצג למשתמש
    if not customer_id and not (guest_name and guest_phone):        # ולידציה: אם אין לקוח קיים, חייבים שם וטלפון
        raise ValueError("חובה לספק שם וטלפון עבור לקוח חדש")         # שגיאה ברורה שתוצג למשתמש

    unique_service_ids = list(dict.fromkeys(service_ids))              # הסרת כפילויות מרשימת מזהי השירותים, תוך שמירת הסדר
    services_selected = get_services_by_ids(unique_service_ids)          # שליפת פרטי כל השירותים שנבחרו מבסיס הנתונים
    if len(services_selected) != len(unique_service_ids):                  # אם לא כל המזהים נמצאו בפועל בטבלה
        raise ValueError("אחד או יותר מהשירותים שנבחרו אינו קיים")           # שגיאה ברורה שתוצג למשתמש

    total_duration = sum(service["default_duration_minutes"] for service in services_selected)  # סכימת משך כל השירותים
    total_price = sum(service["default_price"] for service in services_selected)                   # סכימת מחיר כל השירותים

    if has_conflict(appointment_date, appointment_time, total_duration):  # בדיקת התנגשות עם תור קיים, לפי המשך הכולל
        raise ValueError("קיים כבר תור אחר בטווח הזמן הזה - נא לבחור מועד אחר")  # שגיאת התנגשות

    connection = database.get_connection()                           # פתיחת חיבור לבסיס הנתונים
    cursor = connection.execute(                                        # הכנסת רשומת התור החדש לטבלה, עם הסכומים שחושבו
        "INSERT INTO appointments "
        "(customer_id, guest_name, guest_phone, gender, appointment_date, "
        "appointment_time, duration_minutes, status, price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'ממתין', ?)",
        (customer_id, guest_name, guest_phone, gender,
         appointment_date, appointment_time, total_duration, total_price)
    )
    new_id = cursor.lastrowid                                             # שליפת המזהה שקיבל התור החדש
    for service_id in unique_service_ids:                                   # מעבר על כל שירות שנבחר עבור התור
        connection.execute(                                                   # קישור השירות לתור בטבלת הקישור
            "INSERT INTO appointment_services (appointment_id, service_id) VALUES (?, ?)",
            (new_id, service_id)
        )
    connection.commit()                                                  # שמירת התור וכל קישורי השירותים בפועל
    connection.close()                                                     # סגירת החיבור
    return new_id                                                           # החזרת מזהה התור החדש לקוד הקורא


def list_appointments(status_filter=None, date_filter=None):
    """מחזיר רשימת תורים, כולל שם לקוח/אורח ורשימת שמות השירותים (מחוברים בפסיק), עם אפשרות
    סינון לפי סטטוס ו/או תאריך."""
    query = (                                                    # שאילתה שמצרפת (JOIN) לקוחות ושירותים לתורים
        "SELECT appointments.*, "
        "customers.full_name AS customer_name, "
        "GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN customers ON appointments.customer_id = customers.id "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE 1=1"                                                 # תנאי בסיס נוח שמאפשר להוסיף AND בקלות בהמשך
    )
    params = []                                                       # רשימת הפרמטרים שיוזנו בבטחה לשאילתה
    if status_filter:                                                   # אם התבקש סינון לפי סטטוס
        query += " AND appointments.status = ?"                            # הוספת תנאי הסינון לשאילתה
        params.append(status_filter)                                        # הוספת הערך המתאים לרשימת הפרמטרים
    if date_filter:                                                       # אם התבקש סינון לפי תאריך
        query += " AND appointments.appointment_date = ?"                    # הוספת תנאי הסינון לשאילתה
        params.append(date_filter)                                            # הוספת הערך המתאים לרשימת הפרמטרים
    query += " GROUP BY appointments.id"                                       # קיבוץ לפי תור, כדי לאחד את שמות השירותים
    query += " ORDER BY appointments.appointment_date, appointments.appointment_time"  # מיון כרונולוגי

    connection = database.get_connection()                                # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(query, params).fetchall()                     # הרצת השאילתה עם הפרמטרים הבטוחים
    connection.close()                                                        # סגירת החיבור
    return [dict(row) for row in rows]                                         # המרת כל שורה למילון נוח לשימוש בתבניות


def get_appointment(appointment_id):
    """מחזיר תור בודד לפי מזהה, כולל שם לקוח/אורח ורשימת שמות השירותים, או None אם לא נמצא."""
    connection = database.get_connection()                     # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                     # שליפת תור בודד לפי מזהה, עם JOIN לפרטים נלווים
        "SELECT appointments.*, "
        "customers.full_name AS customer_name, "
        "GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN customers ON appointments.customer_id = customers.id "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE appointments.id = ? "
        "GROUP BY appointments.id",
        (appointment_id,)
    ).fetchone()
    connection.close()                                             # סגירת החיבור
    return dict(row) if row else None                                # החזרת מילון אם נמצאה רשומה, אחרת None


def update_status(appointment_id, new_status):
    """מעדכן את הסטטוס של תור קיים (ממתין / בוצע / בוטל)."""
    if new_status not in ("ממתין", "בוצע", "בוטל"):        # ולידציה: מותרים רק שלושה ערכי סטטוס חוקיים
        raise ValueError("סטטוס לא חוקי")                     # שגיאה ברורה אם התקבל ערך לא צפוי
    connection = database.get_connection()                      # פתיחת חיבור לבסיס הנתונים
    connection.execute(                                            # עדכון שדה הסטטוס של התור המבוקש
        "UPDATE appointments SET status = ? WHERE id = ?",
        (new_status, appointment_id)
    )
    connection.commit()                                              # שמירת השינוי בפועל
    connection.close()                                                 # סגירת החיבור


def delete_appointment(appointment_id):
    """מוחק תור לחלוטין מבסיס הנתונים (משמש לביטול תור עתידי, כפי שנדרש בדרישות)."""
    connection = database.get_connection()                     # פתיחת חיבור לבסיס הנתונים
    connection.execute(                                           # מחיקת השורה המתאימה לתור המבוקש
        "DELETE FROM appointments WHERE id = ?",
        (appointment_id,)
    )
    connection.commit()                                             # שמירת המחיקה בפועל
    connection.close()                                                # סגירת החיבור


def get_next_upcoming_appointment(customer_id):
    """מחזיר את התור העתידי הקרוב ביותר (שממתין, ולא עבר) של לקוח מסוים, או None אם אין לו כזה.
    משמש בעיקר את הצ'אטבוט: אחרי אימות זהות, זה התור ה'אמיתי' שמדווחים עליו ללקוח."""
    import datetime                                                    # ייבוא מקומי לעבודה עם תאריך היום
    today = datetime.date.today().isoformat()                            # תאריך היום, לסינון תורים עתידיים בלבד
    connection = database.get_connection()                                 # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                               # שליפת התור הקרוב ביותר, כולל שמות השירותים
        "SELECT appointments.*, GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE appointments.customer_id = ? AND appointments.status = 'ממתין' "
        "AND appointments.appointment_date >= ? "
        "GROUP BY appointments.id "
        "ORDER BY appointments.appointment_date ASC, appointments.appointment_time ASC "
        "LIMIT 1",
        (customer_id, today)
    ).fetchone()
    connection.close()                                                       # סגירת החיבור
    return dict(row) if row else None                                          # החזרת מילון אם נמצא תור, אחרת None


def list_upcoming_appointments(customer_id):
    """מחזיר את *כל* התורים העתידיים הפתוחים של לקוח/ה מסוים/ת, ממוינים מהקרוב לרחוק.
    נדרש כדי שהצ'אטבוט יוכל לשאול איזה תור לבטל כשיש יותר מאחד - במקום להניח שמדובר בקרוב
    ביותר ולמחוק בטעות את התור הלא נכון."""
    import datetime                                                    # ייבוא מקומי לעבודה עם תאריך היום
    today = datetime.date.today().isoformat()                            # תאריך היום, לסינון תורים עתידיים בלבד
    connection = database.get_connection()                                 # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(                                               # שליפת כל התורים העתידיים, עם שמות השירותים
        "SELECT appointments.*, GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE appointments.customer_id = ? AND appointments.status = 'ממתין' "
        "AND appointments.appointment_date >= ? "
        "GROUP BY appointments.id "
        "ORDER BY appointments.appointment_date ASC, appointments.appointment_time ASC",
        (customer_id, today)
    ).fetchall()
    connection.close()                                                       # סגירת החיבור
    return [dict(row) for row in rows]                                         # המרה לרשימת מילונים


def list_services():
    """מחזיר את רשימת כל השירותים הקיימים בטבלת services (למשל עבור תפריט בחירה בטופס)."""
    connection = database.get_connection()                # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute("SELECT * FROM services ORDER BY name").fetchall()  # שליפת כל השירותים, ממוינים לפי שם
    connection.close()                                       # סגירת החיבור
    return [dict(row) for row in rows]                         # המרת התוצאות לרשימת מילונים נוחה לשימוש


def get_services_by_ids(service_ids):
    """מחזיר את פרטי כל השירותים ששייכים לרשימת מזהים נתונה - משמש לחישוב משך ומחיר כוללים
    כאשר לקוח בוחר כמה שירותים לתור אחד."""
    if not service_ids:                                          # אם לא התקבלה אף רשימה (או שהיא ריקה)
        return []                                                   # אין מה לשלוף - מחזירים רשימה ריקה
    connection = database.get_connection()                        # פתיחת חיבור לבסיס הנתונים
    placeholders = ",".join("?" for _ in service_ids)                # בניית סימני מקום בטוחים (?,?,?...) לפי הכמות
    rows = connection.execute(                                        # שליפת כל השירותים שהמזהה שלהם ברשימה שהתקבלה
        f"SELECT * FROM services WHERE id IN ({placeholders})", list(service_ids)
    ).fetchall()
    connection.close()                                               # סגירת החיבור
    return [dict(row) for row in rows]                                # המרת התוצאות לרשימת מילונים נוחה לשימוש


def get_free_slots(appointment_date, duration_minutes):
    """מחזיר רשימת שעות פנויות (כמחרוזות 'HH:MM') ביום נתון, בהתחשב במשך התור המבוקש."""
    import datetime                                                       # ייבוא מקומי לעבודה עם תאריכים
    weekday = datetime.date.fromisoformat(appointment_date).weekday()       # חישוב יום בשבוע של התאריך המבוקש
    if weekday not in config.OPEN_WEEKDAYS:                                   # אם הקליניקה סגורה באותו יום
        return []                                                               # אין אף משבצת פנויה - רשימה ריקה

    opening = _time_to_minutes(config.OPENING_TIME)      # שעת פתיחה בדקות מתחילת היום
    closing = _time_to_minutes(config.CLOSING_TIME)      # שעת סגירה בדקות מתחילת היום
    slot_length = config.SLOT_LENGTH_MINUTES               # אורך משבצת בסיסית, בדקות

    candidate_times = []                                      # רשימת כל שעות ההתחלה האפשריות ליום זה
    current = opening                                           # מתחילים לבדוק משעת הפתיחה
    while current + duration_minutes <= closing:                  # כל עוד התור נגמר לפני שעת הסגירה
        candidate_times.append(_minutes_to_time(current))           # הוספת המועד המועמד לרשימה
        current += slot_length                                        # מעבר למשבצת הבאה

    free_slots = []                                              # רשימת המשבצות שבאמת פנויות (ללא התנגשות)
    for time_str in candidate_times:                               # בדיקת כל מועד מועמד בנפרד
        if not has_conflict(appointment_date, time_str, duration_minutes):  # אם אין התנגשות עם תור קיים
            free_slots.append(time_str)                                       # המועד פנוי - נוסיף לרשימה הסופית
    return free_slots                                                # החזרת רשימת המשבצות הפנויות
