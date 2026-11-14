import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import stack

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.dest=self.root/'target';self.dest.mkdir();self.src=self.root/'sources';self.src.mkdir()
        (self.dest/'config').write_bytes(b'original identity');(self.dest/'config').chmod(0o640)
        (self.src/'config').write_bytes(b'new bytes');(self.src/'added').write_bytes(b'added library')
        self.plan={'roots':{'system':str(self.dest)},'volume_uuids':{},'files':[]}
        for name in ['config','added']:
            p=self.dest/name
            self.plan['files'].append({'source':str(self.src/name),'scope':'system','relative':name,'target':str(p),
                'before':stack.current_hash(p),'after':stack.sha(self.src/name),'mode':0o644,
                'mode_before':p.stat().st_mode&0o777 if p.exists() else None})
        self.receipt=self.root/'receipt'
    def tearDown(self):self.temp.cleanup()
    def test_install_verify_rollback(self):
        stack.apply(self.plan,self.receipt,False)
        self.assertEqual((self.dest/'added').read_bytes(),b'added library')
        stack.rollback(self.receipt,False)
        self.assertEqual((self.dest/'config').read_bytes(),b'original identity')
        self.assertEqual((self.dest/'config').stat().st_mode&0o777,0o640)
        self.assertFalse((self.dest/'added').exists())
    def test_changed_base_collection_rejected(self):
        base=self.root/'SystemKC';base.write_bytes(b'original kernel collection')
        self.plan['dependencies']=[{'path':str(base),'sha256':stack.sha(base)}]
        base.write_bytes(b'new kernel collection')
        with self.assertRaises(ValueError):stack.apply(self.plan,self.receipt,False)
        self.assertFalse(self.receipt.exists())
    def test_stale_plan_does_not_write(self):
        (self.dest/'config').write_bytes(b'concurrent update')
        with self.assertRaises(ValueError):stack.apply(self.plan,self.receipt,False)
        self.assertFalse(self.receipt.exists());self.assertFalse((self.dest/'added').exists())
    def test_failure_restores_already_written_files(self):
        real=stack.put
        def failing(src,dst,mode,expected=None):
            if Path(dst).name=='added':raise OSError('injected copy failure')
            return real(src,dst,mode,expected)
        with patch.object(stack,'put',side_effect=failing):
            with self.assertRaises(OSError):stack.apply(self.plan,self.receipt,False)
        self.assertEqual((self.dest/'config').read_bytes(),b'original identity')
        self.assertFalse((self.dest/'added').exists())
    def test_rollback_does_not_destroy_later_edits(self):
        stack.apply(self.plan,self.receipt,False);(self.dest/'config').write_bytes(b'later user change')
        with self.assertRaises(ValueError):stack.rollback(self.receipt,False)
        self.assertEqual((self.dest/'config').read_bytes(),b'later user change')
    def test_receipt_path_escape_rejected(self):
        stack.apply(self.plan,self.receipt,False)
        p=self.receipt/'receipt.json';j=json.loads(p.read_text());j['plan']['files'][0]['target']=str(self.root/'outside');p.write_text(json.dumps(j))
        with self.assertRaises(ValueError):stack.rollback(self.receipt,False)
    def test_plan_path_traversal_rejected(self):
        self.plan['files'][0]['relative']='../outside'
        with self.assertRaises(ValueError):stack.apply(self.plan,self.receipt,False)
    def test_symlink_target_rejected(self):
        (self.dest/'added').symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):stack.safe(self.dest,'added')
    def test_new_library_directories_remain_readable_with_restrictive_umask(self):
        import os
        previous=os.umask(0o077)
        try:
            target=self.dest/'new.bundle/Contents/MacOS/library'
            stack.put(self.src/'added',target,0o755)
            self.assertEqual(target.parent.stat().st_mode&0o777,0o755)
            self.assertEqual(target.parent.parent.stat().st_mode&0o777,0o755)
        finally:os.umask(previous)
    def test_copy_cannot_publish_wrong_hash(self):
        with self.assertRaises(ValueError):stack.put(self.src/'added',self.dest/'added',0o644,'0'*64)
        self.assertFalse((self.dest/'added').exists())
    def test_volume_flags_keep_unrelated_arguments(self):
        original={'Kernel Flags':'-v keepsyms=1 slide=0 -NRedNoAccel AMDBacklight=1','Other':'keep'}
        result=stack.graphics_boot_plist(original);flags=result['Kernel Flags'].split()
        for flag in ['-v','keepsyms=1','slide=0','-NRedDonor24','-NRedBootController','-NRedBootAccel','-lilubetaall','AMDBacklight=0']:self.assertIn(flag,flags)
        self.assertNotIn('-NRedNoAccel',flags);self.assertNotIn('AMDBacklight=1',flags)
        self.assertEqual(result['Other'],'keep');self.assertIn('-NRedNoAccel',original['Kernel Flags'])
        self.assertEqual(stack.graphics_boot_plist(result),result)
    def test_identity_and_other_kernel_entries_preserved(self):
        original={'PlatformInfo':{'Generic':{'SystemSerialNumber':'test-only','ROM':b'123456'}},'NVRAM':{'Add':{'sample':{'boot-args':'keep'}}},
                  'Kernel':{'Add':[{'BundlePath':'NootedRed.kext','MaxKernel':'','Enabled':True},{'BundlePath':'Keyboard.kext','Enabled':True}]},'Misc':{'Boot':{'Timeout':5}}}
        result=stack.config_with_donor(original)
        self.assertEqual(original['PlatformInfo'],result['PlatformInfo']);self.assertEqual(original['NVRAM'],result['NVRAM'])
        self.assertEqual(original['Misc'],result['Misc']);self.assertEqual(original['Kernel']['Add'][1],result['Kernel']['Add'][1])
        self.assertEqual(result['Kernel']['Add'][0]['MaxKernel'],'26.99.99');self.assertEqual(original['Kernel']['Add'][0]['MaxKernel'],'')
        self.assertEqual(len(stack.config_with_donor(result)['Kernel']['Add']),3)

if __name__=='__main__':unittest.main(verbosity=2)
