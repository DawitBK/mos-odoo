import base64
import hashlib
import socket
import struct

from odoo import api, fields, models
from odoo.exceptions import UserError

# extension -> how to recognise the content (FR-PLT-060..063)
ALLOWED = {
    'pdf': 'application/pdf',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv',
    'txt': 'text/plain',
}


def sniff(content, ext):
    """The extension a file really has, judged by its first bytes; None when it is not an allowed kind."""
    head = content[:12]
    if head.startswith(b'%PDF'):
        return 'pdf'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if head.startswith(b'PK\x03\x04'):
        return ext if ext in ('docx', 'xlsx') else None
    if ext in ('csv', 'txt') and b'\x00' not in content[:4096]:
        try:
            content[:4096].decode('utf-8')
        except UnicodeDecodeError:
            return None
        return ext
    return None


class MosAttachment(models.Model):
    """A file attached to a MOS record, kept whole and provable: SHA-256 of the content, type checked against the
    first bytes, size limit, optional virus scan, never changed afterwards."""

    _name = 'mos.attachment'
    _inherit = ['mos.audited']
    _description = 'MOS attachment'
    _order = 'id desc'

    name = fields.Char(string='File name', required=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True, index=True)
    file = fields.Binary(required=True, attachment=True)
    sha256 = fields.Char(readonly=True, index=True)
    size = fields.Integer(readonly=True)
    mimetype = fields.Char(readonly=True)
    scan_state = fields.Selection([('clean', 'Clean'), ('not_scanned', 'Not scanned')], default='not_scanned', readonly=True)
    uploaded_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    note = fields.Char()

    @api.model
    def _scan(self, content):
        """Ask ClamAV (clamd) when configured; return 'clean' or 'not_scanned', raise for an infected file."""
        host = self.env['ir.config_parameter'].sudo().get_str('mos_core.clamd_host')
        if not host:
            if self.env['ir.config_parameter'].sudo().get_bool('mos_core.require_scan'):
                raise UserError(self.env._('Virus scanning is required but no scanner is configured.'))
            return 'not_scanned'
        port = self.env['ir.config_parameter'].sudo().get_int('mos_core.clamd_port', 3310)
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(b'zINSTREAM\0')
                for start in range(0, len(content), 65536):
                    chunk = content[start : start + 65536]
                    sock.sendall(struct.pack('!I', len(chunk)) + chunk)
                sock.sendall(struct.pack('!I', 0))
                reply = sock.recv(4096).decode(errors='replace')
        except OSError as error:
            raise UserError(self.env._('The virus scanner could not be reached: %s', error))
        if 'FOUND' in reply:
            raise UserError(self.env._('The file was refused by the virus scanner: %s', reply.strip('\0\n ')))
        return 'clean'

    @api.model_create_multi
    def create(self, vals_list):
        limit_mb = self.env['ir.config_parameter'].sudo().get_int('mos_core.attachment_max_mb', 10)
        for vals in vals_list:
            name = (vals.get('name') or '').strip()
            raw = vals.get('file')
            if not name or not raw:
                raise UserError(self.env._('A file name and the file are required.'))
            content = base64.b64decode(raw)
            if len(content) > limit_mb * 1024 * 1024:
                raise UserError(self.env._('The file is larger than %s MB.', limit_mb))
            if not content:
                raise UserError(self.env._('The file is empty.'))
            ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
            if ext not in ALLOWED:
                raise UserError(self.env._('Files of type "%(ext)s" are not accepted. Allowed: %(allowed)s.', ext=ext, allowed=', '.join(sorted(ALLOWED))))
            detected = sniff(content, ext)
            if not detected or (ALLOWED[detected] != ALLOWED[ext]):
                raise UserError(self.env._('The content of "%s" does not match its file type.', name))
            digest = hashlib.sha256(content).hexdigest()
            if self.sudo().search_count([('res_model', '=', vals['res_model']), ('res_id', '=', vals['res_id']), ('sha256', '=', digest)]):
                raise UserError(self.env._('This exact file is already attached to the record.'))
            vals.update({'sha256': digest, 'size': len(content), 'mimetype': ALLOWED[ext], 'scan_state': self._scan(content)})
            vals['file'] = base64.b64encode(content).decode()
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) & {'file', 'sha256', 'size', 'mimetype', 'res_model', 'res_id', 'name', 'scan_state'}:
            raise UserError(self.env._('An attached file never changes: attach the new version as another file.'))
        return super().write(vals)

    def verify_integrity(self):
        """Recompute the SHA-256 of what is stored; the list of files that no longer match what was recorded."""
        broken = self.browse()
        for att in self:
            stored = att.file
            content = stored.content if hasattr(stored, 'content') else base64.b64decode(stored or b'')
            if hashlib.sha256(content).hexdigest() != att.sha256:
                broken |= att
        return broken
