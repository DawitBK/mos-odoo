from odoo import fields, models, tools


class MosLabQueue(models.Model):
    """The lab's work list: what waits for results or a decision, how long it has waited, and what is out of specification (FR-QC-018)."""

    _name = 'mos.lab.queue'
    _description = 'MOS lab queue'
    _auto = False
    _order = 'age_hours desc'

    inspection_id = fields.Many2one('mos.lab.inspection', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    state = fields.Selection([('open', 'Open'), ('tested', 'Tested'), ('decided', 'Decided'), ('closed', 'Closed')], readonly=True)
    priority = fields.Selection([('normal', 'Normal'), ('urgent', 'Urgent')], readonly=True)
    suggested_outcome = fields.Selection([('incomplete', 'Incomplete'), ('pass', 'Pass'), ('fail', 'Fail')], readonly=True)
    out_of_spec = fields.Boolean(readonly=True)
    waiting = fields.Selection([('results', 'Waiting for results'), ('decision', 'Waiting for the decision')], readonly=True)
    age_hours = fields.Float(readonly=True, aggregator='avg')
    count = fields.Integer(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT i.id AS id, i.id AS inspection_id, i.product_id, i.site_id, i.state, i.priority, i.suggested_outcome,
                       (i.suggested_outcome = 'fail') AS out_of_spec,
                       CASE WHEN i.state = 'open' THEN 'results' ELSE 'decision' END AS waiting,
                       EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - i.create_date)) / 3600.0 AS age_hours,
                       1 AS count
                  FROM mos_lab_inspection i
                 WHERE i.state IN ('open', 'tested')
            )
            """
        )
