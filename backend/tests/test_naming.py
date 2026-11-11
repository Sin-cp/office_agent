"""Deterministic naming rules: safe metadata names and numbered version suffixes."""
import unittest

from app import naming


class NamingTests(unittest.TestCase):
    def test_safe_name_adds_extension_and_sanitizes(self):
        self.assertEqual(naming.safe_name('结婚清单'), '结婚清单.docx')
        self.assertEqual(naming.safe_name('  报告.DOCX '), '报告.docx')
        self.assertEqual(naming.safe_name('a/b:c?.docx'), 'a_b_c_.docx')
        self.assertEqual(naming.safe_name('   '), '新文档.docx')
        self.assertEqual(naming.safe_name('x' * 400).endswith('.docx'), True)

    def test_base_title_strips_stacked_legacy_suffixes(self):
        self.assertEqual(naming.base_title('攻略.docx'), '攻略')
        self.assertEqual(naming.base_title('攻略_修改版.docx'), '攻略')
        self.assertEqual(naming.base_title('攻略_修改版2.docx'), '攻略')
        self.assertEqual(naming.base_title('攻略_修改版_修改版.docx'), '攻略')

    def test_versioned_name_numbers_from_used_versions(self):
        self.assertEqual(naming.versioned_name('攻略.docx', ['攻略.docx']), '攻略_修改版1.docx')
        self.assertEqual(naming.versioned_name('攻略_修改版1.docx', ['攻略.docx', '攻略_修改版1.docx']), '攻略_修改版2.docx')
        self.assertEqual(
            naming.versioned_name('攻略.docx', ['攻略.docx', '攻略_修改版.docx', '攻略_修改版1.docx']),
            '攻略_修改版2.docx')
        self.assertEqual(naming.versioned_name('结婚清单.docx', ['结婚清单.docx']), '结婚清单_修改版1.docx')

    def test_unrelated_siblings_do_not_raise_the_number(self):
        self.assertEqual(
            naming.versioned_name('攻略.docx', ['攻略.docx', '攻略附件_修改版9.docx']),
            '攻略_修改版1.docx')


if __name__ == '__main__':
    unittest.main()
