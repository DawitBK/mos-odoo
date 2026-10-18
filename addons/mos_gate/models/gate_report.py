from odoo import fields, models, tools


class MosGateReport(models.Model):
    """Gate throughput and dwell time by day, site and purpose (FR-GATE-040..042). Read-only SQL view."""

    _name = 'mos.gate.report'
    _description = 'MOS gate report'
    _auto = False
    _order = 'day desc'

    visit_id = fields.Many2one('mos.gate.visit', readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    day = fields.Date(readonly=True)
    purpose = fields.Selection([('inbound_po', 'Inbound: PO delivery'), ('outbound_do', 'Outbound: customer delivery')], readonly=True)
    state = fields.Selection(
        [('expected', 'Expected'), ('arrived', 'Arrived'), ('weighing', 'Weighing'), ('weighed', 'Weighed'), ('cleared', 'Exit cleared'), ('exited', 'Exited'), ('cancelled', 'Cancelled')],
        readonly=True,
    )
    visits = fields.Integer(readonly=True, aggregator='sum')
    net_weight = fields.Float(string='Net weight (kg)', readonly=True, aggregator='sum')
    dwell_minutes = fields.Float(string='Minutes on site', readonly=True, aggregator='avg')
    incidents = fields.Integer(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT v.id AS id,
                       v.id AS visit_id,
                       v.site_id,
                       v.arrived_at::date AS day,
                       v.purpose,
                       v.state,
                       1 AS visits,
                       COALESCE(t.net_weight, 0) AS net_weight,
                       CASE WHEN v.exited_at IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (v.exited_at - v.arrived_at)) / 60.0 END AS dwell_minutes,
                       (SELECT COUNT(*) FROM mos_gate_incident i WHERE i.visit_id = v.id) AS incidents
                  FROM mos_gate_visit v
             LEFT JOIN mos_weigh_ticket t ON t.id = v.ticket_id
                 WHERE v.state <> 'cancelled'
            )
            """
        )
