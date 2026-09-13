from __future__ import annotations
import hashlib
import unittest
from uuid import uuid4

from interloc.evidence import EvidenceError, StreamingSanitizer, build_text_group, local_image_descriptor, safe_generated_path, text_group_export


class EvidenceTests(unittest.TestCase):
    def test_split_secret_never_leaks(self):
        secret="tok_SUPER_SECRET_123"
        s=StreamingSanitizer(secrets=[secret])
        output="".join(s.feed(ch) for ch in "before "+secret+" after")+s.finish()
        self.assertNotIn(secret,output); self.assertEqual(output,"before [REDACTED] after")

    def test_split_personal_path_variants_redact(self):
        path=r"C:\Users\alice\private"
        s=StreamingSanitizer(personal_paths=[path])
        parts=[r"x C:\Users\ali",r"ce\private y C:/Users/alice/private z"]
        output="".join(s.feed(x) for x in parts)+s.finish()
        self.assertNotIn("alice",output); self.assertEqual(output.count("[REDACTED]"),2)

    def test_ansi_csi_osc_hyperlink_and_clipboard_payload_are_removed_across_fragments(self):
        fragments=["a\x1b[31", "mRED\x1b[0m b\x1b]8;;https://evil", ".example\x1b\\LINK\x1b]8;;\x1b\\ c\x1b]52;c;c2Vj", "cmV0\x07d"]
        s=StreamingSanitizer(); output="".join(s.feed(x) for x in fragments)+s.finish()
        self.assertEqual(output,"aRED bLINK cd"); self.assertNotIn("evil",output); self.assertNotIn("c2Vj",output)

    def test_incomplete_escape_is_dropped(self):
        s=StreamingSanitizer(); output=s.feed("safe\x1b]52;c;secret")+s.finish()
        self.assertEqual(output,"safe")

    def test_chunks_are_utf8_bounded_and_hash_exact(self):
        group=build_text_group(["é"*20],max_chunk_bytes=7,max_group_bytes=100)
        self.assertFalse(group.truncated); self.assertEqual(group.sanitized_bytes,40)
        for chunk in group.chunks:
            data=chunk.text.encode(); self.assertLessEqual(len(data),7); self.assertEqual(hashlib.sha256(data).hexdigest(),chunk.sha256)
        self.assertEqual(b"".join(c.text.encode() for c in group.chunks),("é"*20).encode())

    def test_group_limit_is_explicit_truncation(self):
        group=build_text_group(["x"*100],max_chunk_bytes=16,max_group_bytes=25)
        self.assertTrue(group.truncated); self.assertEqual(group.sanitized_bytes,25); self.assertEqual(group.dropped_sanitized_bytes,75)
        self.assertEqual(sum(c.byte_length for c in group.chunks),25)

    def test_seeded_secret_absent_from_all_exported_bytes(self):
        secret="SEED-DO-NOT-EXPORT"
        group=build_text_group(["prefix ",secret[:5],secret[5:]," suffix"],secrets=[secret],max_chunk_bytes=8)
        files,manifest=text_group_export(group,str(uuid4()))
        combined=b"".join(files.values())
        self.assertNotIn(secret.encode(),combined); self.assertIn(b"[REDACTED]",combined)
        self.assertFalse(manifest["truncated"])
        for entry in manifest["entries"]: self.assertTrue(safe_generated_path(entry["path"]))

    def test_export_paths_and_entries_bind_exact_bytes(self):
        group=build_text_group(["hello"," world"],max_chunk_bytes=5)
        files,manifest=text_group_export(group,str(uuid4()))
        self.assertEqual(len(files),len(manifest["entries"]))
        for entry in manifest["entries"]:
            data=files[entry["path"]]; self.assertEqual(len(data),entry["byte_length"]); self.assertEqual(hashlib.sha256(data).hexdigest(),entry["sha256"])

    def test_local_image_descriptor_cannot_claim_remote_delivery(self):
        payload=b"\x89PNG\r\n\x1a\nfixture"
        d=local_image_descriptor(payload)
        self.assertEqual(d["delivery_state"],"local_only"); self.assertEqual(d["redaction_status"],"review_required"); self.assertIn("local_artifact_id",d); self.assertNotIn("mailbox_path",d)
        self.assertEqual(d["sha256"],hashlib.sha256(payload).hexdigest())

    def test_image_size_and_media_type_are_bounded(self):
        with self.assertRaises(EvidenceError): local_image_descriptor(b"x",media_type="image/jpeg")
        with self.assertRaises(EvidenceError): local_image_descriptor(b"x"*(16*1024*1024+1))

    def test_secret_configuration_is_bounded(self):
        with self.assertRaises(EvidenceError): StreamingSanitizer(secrets=[""])
        with self.assertRaises(EvidenceError): StreamingSanitizer(secrets=[str(i) for i in range(65)])

    def test_huge_line_is_bounded_without_control_sequences(self):
        group=build_text_group(["a"*1_000_000],max_group_bytes=4096)
        self.assertTrue(group.truncated); self.assertEqual(group.sanitized_bytes,4096); self.assertLessEqual(sum(c.byte_length for c in group.chunks),4096)

if __name__=="__main__": unittest.main()
