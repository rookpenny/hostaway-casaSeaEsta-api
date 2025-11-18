import os
from pyairtable import Table

AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")

def get_pmcs_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "PMC")

def get_properties_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Properties")

def get_messages_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Guest Messages")

def get_prearrival_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Prearrival Options")

def get_guests_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Guests")

def get_house_manuals_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "HouseManuals")

def get_usage_logs_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Usage Logs")

def get_billing_table():
    return Table(AIRTABLE_API_KEY, BASE_ID, "Billing")
