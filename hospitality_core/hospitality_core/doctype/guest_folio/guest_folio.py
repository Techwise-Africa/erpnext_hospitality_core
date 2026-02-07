import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class GuestFolio(Document):

    def autoname(self):
        # Different Naming for Company Master Folios
        if self.is_company_master:
            # e.g., MASTER-GOOGLE
            # We sanitize the company name
            company_key = self.company.replace(" ", "")[:10].upper()
            self.name = make_autoname(f"MASTER-{company_key}-.#####")
        else:
            # Standard Guest Folio
            self.name = make_autoname("FOLIO-.#####")

    def validate(self):
        self.validate_status_change()
        self.validate_master_folio()

    def validate_master_folio(self):
        if self.is_company_master and not self.company:
            frappe.throw(_("Company is mandatory for a Company Master Folio."))

        if not self.is_company_master and not self.reservation:
            # Regular guest folios usually need a reservation
            pass

    def validate_status_change(self):
        if self.status == "Closed":
            # Check if this folio belongs to a Company Guest
            is_company_guest = False

            if self.reservation:
                is_company_guest = frappe.db.get_value(
                    "Hotel Reservation",
                    self.reservation,
                    "is_company_guest"
                )

            # If NOT a company guest, enforce balance settlement
            if not is_company_guest:
                if self.outstanding_balance > 0.01:
                    frappe.throw(
                        _("Cannot Close Folio. Outstanding Balance is {0}. "
                          "Please settle payments or post allowances.")
                        .format(self.outstanding_balance)
                    )

    def after_save(self):
        if self.status == "Closed":
            from hospitality_core.hospitality_core.api.folio import record_guest_balance
            record_guest_balance(self)

    def on_cancel(self):
        # Reverse balances or transactions here
        pass

    def on_trash(self):
        if not frappe.has_permission("Guest Folio", "delete"):
            frappe.throw(_("You do not have permission to delete this Folio."))

        if self.transactions and frappe.session.user != "Administrator":
            frappe.throw(_("Only Administrator can delete Folios with transactions."))
