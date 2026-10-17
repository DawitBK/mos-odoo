import hashlib
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# One advisory lock key serialises writers so the chain has exactly one order (FR-PLT-046).
_CHAIN_LOCK = 70042001
GENESIS = '0' * 64


class MosAuditEvent(models.Model):
    """Tamper-evident audit log: every event stores the hash of the one before it.

    The table is append-only: a database trigger refuses UPDATE and DELETE. The application owner can
    still drop the trigger (Odoo runs as the database owner, see ODOO-FIT-GAP section 4), so the
    daily verification, and an external copy of the latest hash, are what make tampering visible.
    """

    _name = 'mos.audit.event'
    _description = 'MOS audit event'
    _order = 'id'
    _log_access = False

    occurred_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='restrict')
    action = fields.Char(required=True, readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    summary = fields.Char(readonly=True)
    prev_hash = fields.Char(required=True, readonly=True)
    hash = fields.Char(required=True, readonly=True)

    def init(self):
        self.env.cr.execute(
            """
            CREATE OR REPLACE FUNCTION mos_audit_block_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'mos_audit_event is append-only';
            END;
            $$ LANGUAGE plpgsql;
            DROP TRIGGER IF EXISTS mos_audit_no_update ON mos_audit_event;
            CREATE TRIGGER mos_audit_no_update BEFORE UPDATE OR DELETE ON mos_audit_event
                FOR EACH ROW EXECUTE FUNCTION mos_audit_block_change();
            """
        )

    @staticmethod
    def _digest(prev_hash, payload):
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256((prev_hash + body).encode()).hexdigest()

    @api.model
    def record(self, action, record=None, summary=''):
        """Append one event to the chain and return it."""
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', [_CHAIN_LOCK])
        self.env.cr.execute('SELECT hash FROM mos_audit_event ORDER BY id DESC LIMIT 1')
        row = self.env.cr.fetchone()
        prev = row[0] if row else GENESIS
        occurred = fields.Datetime.now()
        values = {
            'occurred_at': occurred,
            'user_id': self.env.uid,
            'action': action,
            'res_model': record._name if record else False,
            'res_id': record.id if record else 0,
            'summary': (summary or '')[:240],
        }
        payload = dict(values, occurred_at=fields.Datetime.to_string(occurred))
        return self.sudo().create(dict(values, prev_hash=prev, hash=self._digest(prev, payload)))

    @api.model
    def verify_chain(self):
        """Return None when the chain is intact, else the id of the first event that does not verify."""
        prev = GENESIS
        self.env.cr.execute(
            'SELECT id, occurred_at, user_id, action, res_model, res_id, summary, prev_hash, hash '
            'FROM mos_audit_event ORDER BY id'
        )
        for eid, occurred, uid, action, res_model, res_id, summary, prev_hash, digest in self.env.cr.fetchall():
            payload = {
                'occurred_at': fields.Datetime.to_string(occurred),
                'user_id': uid,
                'action': action,
                'res_model': res_model or False,
                'res_id': res_id or 0,
                'summary': summary or '',
            }
            if prev_hash != prev or self._digest(prev, payload) != digest:
                return eid
            prev = digest
        return None

    @api.model
    def _cron_verify_chain(self):
        broken = self.verify_chain()
        if broken:
            _logger.error('MOS audit chain is BROKEN at event %s', broken)
        else:
            _logger.info('MOS audit chain verified')
        return broken
