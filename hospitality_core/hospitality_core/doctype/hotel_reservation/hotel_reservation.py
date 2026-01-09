import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate, flt

from hospitality_core.hospitality_core.api.reservation import (
    check_availability,
    create_folio,
)

from hospitality_core.hospitality_core.api.night_audit import (
    post_room_charge,
    get_rate,
    already_charged_today,
)


class HotelReservation(Document):

    def validate(self):
        self.validate_dates()

        # Only validate availability if status is Reserved or Checked In
        if self.status in ["Reserved", "Checked In"]:
            self.validate_room_availability()

        # Company guest validation
        if self.is_company_guest and not self.company:
            frappe.throw(_("Company is mandatory when 'Is Company Guest' is checked."))

        if self.company:
            self.ensure_company_folio()

    def validate_dates(self):
        if getdate(self.arrival_date) >= getdate(self.departure_date):
            frappe.throw(_("Departure Date must be after Arrival Date."))

    def validate_room_availability(self):
        check_availability(
            room=self.room,
            arrival_date=self.arrival_date,
            departure_date=self.departure_date,
            ignore_reservation=self.name,
        )

    def after_insert(self):
        # Create folio for guest
        create_folio(self)

    def ensure_company_folio(self):
        """Ensures an OPEN Master Folio exists for the Company."""

        if not self.company:
            return

        exists = frappe.db.exists(
            "Guest Folio",
            {
                "company": self.company,
                "status": "Open",
                "is_company_master": 1,
            },
        )

        if not exists:
            guest_name = self.get_corporate_guest_name()

            folio = frappe.new_doc("Guest Folio")
            folio.is_company_master = 1
            folio.guest = guest_name
            folio.company = self.company
            folio.status = "Open"
            folio.open_date = nowdate()
            folio.insert(ignore_permissions=True)

            frappe.msgprint(
                _("Created new Master Folio for Company: {0}").format(self.company)
            )

    def get_corporate_guest_name(self):
        """Gets or creates representative Guest record."""

        g_name = frappe.db.get_value(
            "Guest", {"customer": self.company}, "name"
        )

        if not g_name:
            cust = frappe.get_doc("Customer", self.company)

            g = frappe.new_doc("Guest")
            g.full_name = f"{cust.customer_name} (Master Rep)"
            g.customer = self.company
            g.guest_type = "Corporate"
            g.insert(ignore_permissions=True)

            g_name = g.name

        return g_name

    def process_check_in(self):

        if self.status != "Reserved":
            frappe.throw(_("Only Reserved bookings can be Checked In."))

        if getdate(self.arrival_date) > getdate(nowdate()):
            frappe.throw(_("Cannot Check-In before Arrival Date."))

        self.db_set("status", "Checked In")

        frappe.db.set_value("Hotel Room", self.room, "status", "Occupied")

        if self.folio:
            frappe.db.set_value("Guest Folio", self.folio, "status", "Open")

            if not already_charged_today(self.folio, nowdate()):
                rate = get_rate(
                    self.rate_plan,
                    self.room_type,
                    nowdate(),
                )

                if rate > 0:
                    post_room_charge(self, rate, nowdate())

                    frappe.msgprint(
                        _("Check-in successful. Room charged {0}.").format(rate)
                    )

        return "Checked In"

    def process_check_out(self):

        if self.status != "Checked In":
            frappe.throw(_("Guest is not currently Checked In."))

        if getdate(self.departure_date) != getdate(nowdate()):
            frappe.throw(
                _("Departure date must be today.")
            )

        # GROUP CHECK
        if self.is_group_guest and self.group_booking:
            master_folio = frappe.db.get_value(
                "Hotel Group Booking",
                self.group_booking,
                "master_folio",
            )

            if master_folio:
                from hospitality_core.hospitality_core.api.folio import (
                    sync_folio_balance,
                )

                master_doc = frappe.get_doc("Guest Folio", master_folio)
                sync_folio_balance(master_doc)

                balance = frappe.db.get_value(
                    "Guest Folio",
                    master_folio,
                    "outstanding_balance",
                )

                if balance > 0.01:
                    frappe.throw(
                        _("Group Master Folio has outstanding balance: {0}").format(
                            balance
                        )
                    )

        # HANDLE FOLIO
        if self.folio:
            folio_doc = frappe.get_doc("Guest Folio", self.folio)

            # COMPANY TRANSFER
            if self.company:
                company_liability = frappe.db.sql(
                    """
                    SELECT SUM(amount)
                    FROM `tabFolio Transaction`
                    WHERE parent=%s
                    AND bill_to='Company'
                    AND is_void=0
                """,
                    (self.folio,),
                )[0][0] or 0

                if company_liability > 0:

                    transfer_item = "TRANSFER"

                    if not frappe.db.exists("Item", transfer_item):
                        item = frappe.new_doc("Item")
                        item.item_code = transfer_item
                        item.item_name = "Transfer to City Ledger"
                        item.item_group = "Services"
                        item.is_stock_item = 0
                        item.insert(ignore_permissions=True)

                    frappe.get_doc(
                        {
                            "doctype": "Folio Transaction",
                            "parent": self.folio,
                            "parenttype": "Guest Folio",
                            "parentfield": "transactions",
                            "posting_date": nowdate(),
                            "item": transfer_ite_
