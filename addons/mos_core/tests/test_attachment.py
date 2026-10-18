import base64
import zipfile
from io import BytesIO

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def b64(data):
    return base64.b64encode(data).decode()


def zipped():
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<x/>')
    return buf.getvalue()


@tagged('post_install', '-at_install')
class TestAttachment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.site = cls.env['mos.site'].create({'name': 'Mill', 'code': 'MILLA', 'warehouse_id': cls.env['stock.warehouse'].search([], limit=1).id})

    def _attach(self, name, content, **extra):
        return self.env['mos.attachment'].create({'name': name, 'res_model': 'mos.site', 'res_id': self.site.id, 'file': b64(content), **extra})

    def test_a_valid_file_is_stored_with_its_hash(self):
        att = self._attach('contract.pdf', PDF)
        self.assertEqual(len(att.sha256), 64)
        self.assertEqual(att.size, len(PDF))
        self.assertEqual(att.mimetype, 'application/pdf')
        self.assertEqual(att.scan_state, 'not_scanned')
        self.assertFalse(att.verify_integrity())

    def test_type_must_match_the_content(self):
        with self.assertRaises(UserError):
            self._attach('invoice.pdf', b'MZ\x90\x00 this is a program')  # an executable named .pdf
        with self.assertRaises(UserError):
            self._attach('photo.png', PDF)  # a pdf named .png
        with self.assertRaises(UserError):
            self._attach('tool.exe', b'MZ\x90\x00')  # not an allowed extension
        with self.assertRaises(UserError):
            self._attach('data.csv', b'a,b\n\x00\x01\x02')  # binary pretending to be csv
        self.assertTrue(self._attach('scan.png', PNG))
        self.assertTrue(self._attach('sheet.xlsx', zipped()))
        self.assertTrue(self._attach('rows.csv', b'a,b\n1,2\n'))

    def test_size_limit_empty_and_duplicates(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_core.attachment_max_mb', '1')
        with self.assertRaises(UserError):
            self._attach('big.pdf', PDF + b'0' * (1024 * 1024))
        with self.assertRaises(UserError):
            self._attach('empty.txt', b'')
        self._attach('one.pdf', PDF)
        with self.assertRaises(UserError):
            self._attach('same-content.pdf', PDF)  # exact duplicate on the same record

    def test_attachments_never_change(self):
        att = self._attach('contract.pdf', PDF)
        with self.assertRaises(UserError):
            att.write({'file': b64(PDF + b'x')})
        with self.assertRaises(UserError):
            att.write({'name': 'renamed.pdf'})
        att.write({'note': 'signed copy'})  # a note is the only editable thing
        self.assertEqual(att.note, 'signed copy')

    def test_integrity_check_detects_tampering(self):
        att = self._attach('contract.pdf', PDF)
        self.env.cr.execute("UPDATE mos_attachment SET sha256 = %s WHERE id = %s", ['0' * 64, att.id])
        att.invalidate_recordset()
        self.assertEqual(att.verify_integrity(), att)

    def test_scan_is_required_when_policy_says_so(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_core.require_scan', 'True')
        with self.assertRaises(UserError):
            self._attach('contract.pdf', PDF)
