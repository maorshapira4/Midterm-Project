"""
seed_demo_data.py - סקריפט שמזין דאטת דמו אמיתית (לקוחות, תורים ולידים) לצורך בדיקת הצ'אטבוט
של פרויקט הסיום. רץ אוטומטית בכל הפעלה של האפליקציה (מתוך app.py), בדיוק כמו seed_services.py,
אבל בודק בעצמו האם הדאטה כבר קיימת (לפי מספר טלפון) לפני שהוא מכניס אותה מחדש - כדי שהרצה חוזרת
של האפליקציה (כל restart) לא תיצור כפילויות. אפשר גם להריץ ידנית: python seed_demo_data.py

הדאטה כאן נבחרה בכוונה כדי לכסות את כל תרחישי הבדיקה שנדרשים בפרויקט הסיום:
- שני לקוחות בשם פרטי זהה ("רותם מירון" ו"רותם כהן") - לתרחיש אי-חד-משמעיות בזיהוי.
- לקוח עם שם ייחודי ("דנה לוי") ותור אמיתי - לתרחיש "שם ייחודי + תאריך שגוי שהמשתמש טוען".
- לקוח קיים בלי אף תור פתוח ("מאיה בן דוד") - לתרחיש המתאים.
- לכל הלקוחות תעודת זהות תקינת-פורמט (9 ספרות), הנדרשת לאימות בצ'אטבוט.
"""

import database                       # קובץ הגישה לבסיס הנתונים
import models.customers as customers    # הוספת לקוחות ובדיקה אם לקוח כבר קיים
import models.appointments as appointments  # הוספת תורים ועדכון סטטוס
import models.leads as leads              # הוספת לידים והמרת ליד ללקוח

# רשימת לקוחות הדמו: (שם מלא, טלפון, תעודת זהות)
DEMO_CUSTOMERS = [
    ("רותם מירון", "0501234567", "123456789"),
    ("רותם כהן", "0502345678", "234567890"),
    ("דנה לוי", "0503456789", "345678901"),
    ("יוסי אברהם", "0504567890", "456789012"),
    ("מאיה בן דוד", "0505678901", "567890123"),   # בכוונה לא תקבל אף תור - לתרחיש "לקוח בלי תורים"
    ("אלון פרץ", "0506789012", "678901234"),
]

# תורים לדמו: (טלפון לקוח, תאריך, שעה, מגדר, רשימת שמות שירותים). ריקה עבור "מאיה בן דוד" בכוונה.
DEMO_APPOINTMENTS = [
    ("0501234567", "2026-09-10", "10:00", "אישה", ["לק ג'ל"]),
    ("0502345678", "2026-09-15", "11:00", "אישה", ["מניקור"]),
    ("0503456789", "2026-10-05", "16:00", "אישה", ["טיפול פנים"]),
    ("0504567890", "2026-09-20", "09:30", "גבר", ["פדיקור"]),
    ("0506789012", "2026-11-02", "17:00", "גבר", ["הסרת שיער בשעווה"]),
]

# תורים נוספים בעבר, רק כדי שטבלת appointments תכלול גם את הסטטוסים "בוצע" ו"בוטל" (לא רק "ממתין"):
# (טלפון לקוח, תאריך, שעה, מגדר, רשימת שמות שירותים, הסטטוס הסופי שיוגדר להם)
DEMO_PAST_APPOINTMENTS = [
    ("0501234567", "2026-06-01", "10:00", "אישה", ["מניקור"], "בוצע"),
    ("0504567890", "2026-06-10", "12:00", "גבר", ["לק ג'ל"], "בוטל"),
]

# רשימת לידים לדמו: (שם מלא, טלפון, מקור, סטטוס סופי רצוי)
DEMO_LEADS = [
    ("שירה גולן", "0507000001", "אינסטגרם", "חדש"),
    ("עידן שפירא", "0507000002", "המלצה", "בטיפול"),
    ("נועה בר", "0507000003", "טלפון", "הפך ללקוח"),   # ממומש בפועל דרך המרת ליד ללקוח, לא רק עדכון סטטוס
    ("גיא רוזן", "0507000004", "אתר", "נדחה"),
    ("תמר אזולאי", "0507000005", "פייסבוק", "חדש"),
]


def _seed_customers_and_appointments():
    """מזין את לקוחות הדמו ואת התורים שלהם, רק אם הם עדיין לא קיימים (לפי מספר טלפון)."""
    sentinel_phone = DEMO_CUSTOMERS[0][1]                           # טלפון הלקוח הראשון ברשימה, משמש כ"דגל" לבדיקת קיום
    if customers.find_customer_by_phone(sentinel_phone):              # אם הלקוח הראשון כבר קיים - כל האצווה כבר הוזנה בעבר
        return                                                           # אין צורך להזין שוב, יוצאים מהפונקציה

    phone_to_id = {}                                                  # מיפוי טלפון -> מזהה לקוח, לשימוש בהזנת התורים בהמשך
    for full_name, phone, id_number in DEMO_CUSTOMERS:                  # מעבר על כל לקוח דמו
        customer_id = customers.add_customer(                            # הוספת הלקוח בפועל
            full_name=full_name, phone=phone, id_number=id_number
        )
        phone_to_id[phone] = customer_id                                   # שמירת המזהה החדש למיפוי

    service_name_to_id = {                                               # מיפוי שם שירות -> מזהה שירות, לשימוש בהמשך
        service["name"]: service["id"] for service in appointments.list_services()
    }

    for phone, date, time, gender, service_names in DEMO_APPOINTMENTS:    # מעבר על כל תור עתידי בדמו
        service_ids = [service_name_to_id[name] for name in service_names]  # המרת שמות השירותים למזהים
        appointments.add_appointment(                                        # הוספת התור בפועל
            customer_id=phone_to_id[phone], guest_name=None, guest_phone=None,
            gender=gender, service_ids=service_ids,
            appointment_date=date, appointment_time=time,
        )

    for phone, date, time, gender, service_names, final_status in DEMO_PAST_APPOINTMENTS:  # תורי עבר, לגיוון סטטוסים
        service_ids = [service_name_to_id[name] for name in service_names]
        appointment_id = appointments.add_appointment(
            customer_id=phone_to_id[phone], guest_name=None, guest_phone=None,
            gender=gender, service_ids=service_ids,
            appointment_date=date, appointment_time=time,
        )
        appointments.update_status(appointment_id, final_status)             # עדכון הסטטוס לסופי (בוצע/בוטל)


def _seed_leads():
    """מזין את לידי הדמו, רק אם הם עדיין לא קיימים (לפי מספר טלפון)."""
    sentinel_phone = DEMO_LEADS[0][1]                                # טלפון הליד הראשון ברשימה, משמש כ"דגל" לבדיקת קיום
    connection = database.get_connection()                             # פתיחת חיבור זמני, רק לבדיקת קיום מהירה
    already_exists = connection.execute(                                 # בדיקה האם כבר קיים ליד עם הטלפון הזה
        "SELECT id FROM leads WHERE phone = ?", (sentinel_phone,)
    ).fetchone()
    connection.close()                                                    # סגירת החיבור הזמני
    if already_exists:                                                     # אם האצווה כבר הוזנה בעבר
        return                                                                # אין צורך להזין שוב

    for full_name, phone, source, final_status in DEMO_LEADS:            # מעבר על כל ליד דמו
        lead_id = leads.add_lead(full_name=full_name, phone=phone, source=source)  # הוספת הליד (מתחיל כ"חדש")
        if final_status == "הפך ללקוח":                                     # מקרה מיוחד: המרה אמיתית ללקוח, לא רק עדכון סטטוס
            leads.convert_lead_to_customer(lead_id)
        elif final_status != "חדש":                                          # לכל סטטוס אחר שאינו ברירת המחדל
            leads.update_lead_status(lead_id, final_status)


def seed_demo_data():
    """נקודת הכניסה הראשית של הסקריפט: מזינה לקוחות, תורים ולידים לדוגמה, בצורה בטוחה להרצה חוזרת."""
    _seed_customers_and_appointments()   # הזנת לקוחות ותורי הדמו
    _seed_leads()                          # הזנת לידי הדמו


if __name__ == "__main__":       # הבלוק הזה רץ רק כאשר מריצים את הקובץ הזה ישירות (לא כשהוא מיובא)
    database.init_db()             # וידוא שכל הטבלאות קיימות לפני שמנסים להזין אליהן נתונים
    seed_demo_data()                  # הרצת ההזנה בפועל
    print("דאטת הדמו (לקוחות, תורים ולידים) הוזנה בהצלחה לבסיס הנתונים.")  # הודעת אישור ידידותית
