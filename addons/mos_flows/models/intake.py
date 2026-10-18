from odoo import fields, models


class MosFlowIntakeWizard(models.TransientModel):
    """Flow C, step one: the lab opens an inspection lot against a purchase order and a truck (FR-QC-025)."""

    _name = 'mos.flow.intake.wizard'
    _description = 'MOS lab intake (flow C)'

    purchase_id = fields.Many2one('purchase.order', required=True, domain="[('state', 'in', ('purchase', 'done'))]")
    plate = fields.Char(required=True)
    driver_name = fields.Char()
    delivery_note = fields.Char(string="Supplier's delivery note")
    bags = fields.Integer()
    gross = fields.Float(string='Gross weight (kg)', digits=(12, 1), required=True)
    tare = fields.Float(string='Tare weight (kg)', digits=(12, 1), required=True, help='Stored or provisional tare when the truck is not weighed here.')
    silo_id = fields.Many2one('mos.silo', string='Unload into silo')

    def action_confirm(self):
        self.ensure_one()
        visit, inspection = self.env['mos.gate.visit'].lab_intake(
            self.purchase_id,
            self.plate,
            self.gross,
            self.tare,
            driver_name=self.driver_name or '',
            delivery_note=self.delivery_note or '',
            bags=self.bags,
            silo=self.silo_id,
        )
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.lab.inspection', 'res_id': inspection.id, 'view_mode': 'form', 'target': 'current'}
