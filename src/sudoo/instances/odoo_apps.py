"""The standard Odoo apps offered by the module picker of a new instance (GET /api/v1/odoo-apps).

A list kept by hand, by technical name: what Odoo's Apps menu shows as an application, Community first, then the
Enterprise ones (offered only for an Enterprise instance). Each app says from which major version on it exists
(`since`) and the last one that has it (`until`), so one list serves every version the panel offers.

Keeping it current: when a new Odoo version adds an app, add a line with `since` set to that version; when it drops or
renames one, set `until` on the old line and add the new name. Only well-known apps belong here: anything else can be
typed in the picker's "Other" field. Odoo installs each app's dependencies itself, so none are listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class App:
    name: str  # technical name, as passed to `odoo -i`
    title: str  # as Odoo's Apps menu shows it
    enterprise: bool = False
    since: int = 0  # first major version with the app (0: every version the panel offers)
    until: int = 0  # last major version with the app (0: still there)


APPS: tuple[App, ...] = (
    # Community (also part of every Enterprise database)
    App("sale_management", "Sales"),
    App("account", "Invoicing"),
    App("crm", "CRM"),
    App("stock", "Inventory"),
    App("purchase", "Purchase"),
    App("mrp", "Manufacturing"),
    App("point_of_sale", "Point of Sale"),
    App("website", "Website"),
    App("website_sale", "eCommerce"),
    App("website_blog", "Blog"),
    App("website_forum", "Forum"),
    App("website_slides", "eLearning"),
    App("project", "Project"),
    App("hr_timesheet", "Timesheets"),
    App("hr", "Employees"),
    App("hr_holidays", "Time Off"),
    App("hr_attendance", "Attendances"),
    App("hr_expense", "Expenses"),
    App("hr_recruitment", "Recruitment"),
    App("hr_skills", "Skills Management"),
    App("mail", "Discuss"),
    App("calendar", "Calendar"),
    App("contacts", "Contacts"),
    App("im_livechat", "Live Chat"),
    App("mass_mailing", "Email Marketing"),
    App("event", "Events"),
    App("survey", "Surveys"),
    App("fleet", "Fleet"),
    App("maintenance", "Maintenance"),
    App("repair", "Repairs"),
    App("lunch", "Lunch"),
    App("note", "Notes", until=16),
    App("project_todo", "To-do", since=17),
    App("spreadsheet_dashboard", "Dashboards", since=16),
    # Enterprise only
    App("accountant", "Accounting", enterprise=True),
    App("helpdesk", "Helpdesk", enterprise=True),
    App("planning", "Planning", enterprise=True),
    App("timesheet_grid", "Timesheets (Enterprise)", enterprise=True),
    App("industry_fsm", "Field Service", enterprise=True),
    App("sale_subscription", "Subscriptions", enterprise=True),
    App("sale_renting", "Rental", enterprise=True),
    App("documents", "Documents", enterprise=True),
    App("sign", "Sign", enterprise=True),
    App("knowledge", "Knowledge", enterprise=True, since=16),
    App("approvals", "Approvals", enterprise=True),
    App("appointment", "Appointments", enterprise=True, since=16),
    App("website_calendar", "Appointments", enterprise=True, until=15),
    App("quality_control", "Quality", enterprise=True),
    App("mrp_plm", "PLM", enterprise=True),
    App("stock_barcode", "Barcode", enterprise=True),
    App("hr_payroll", "Payroll", enterprise=True),
    App("hr_appraisal", "Appraisals", enterprise=True),
    App("hr_referral", "Referrals", enterprise=True),
    App("marketing_automation", "Marketing Automation", enterprise=True),
    App("social", "Social Marketing", enterprise=True),
    App("voip", "VoIP", enterprise=True),
    App("frontdesk", "Frontdesk", enterprise=True, since=17),
    App("web_studio", "Studio", enterprise=True),
)


def major(version: str) -> int:
    """The major version of "18.0" or "saas-18.2" (18); 0 when there is none."""
    found = re.search(r"(\d+)\.\d+", version or "")
    return int(found.group(1)) if found else 0


def apps(version: str, edition: str) -> list[dict[str, Any]]:
    """The apps offered for a new instance of Odoo `version` and `edition`: [{"name", "title", "enterprise"}],
    Community first. Enterprise apps only for "enterprise"."""
    number = major(version)
    enterprise = edition == "enterprise"
    return [
        {"name": app.name, "title": app.title, "enterprise": app.enterprise}
        for app in APPS
        if (enterprise or not app.enterprise)
        and (not number or ((not app.since or number >= app.since) and (not app.until or number <= app.until)))
    ]


def names(version: str, edition: str) -> set[str]:
    return {app["name"] for app in apps(version, edition)}
