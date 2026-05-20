from __future__ import annotations

import unittest

from app.services.ab_token import build_ab_token_headers


class ABTokenTests(unittest.TestCase):
    def test_json_body_matches_document_example(self) -> None:
        headers = build_ab_token_headers(
            access_key="31c2602c-56a8-49e0-9709-746d8b2c8bd9",
            secret_key="e02a4168-e6e7-4a5b-a8a5-4cbcd6beda0d",
            content_type="application/json",
            body='{"a":1,"b":2}',
            request_id="19005119-88cf-11e8-8227-90e2ba1fdc4b",
            sign_time="2022-05-10 18:13:14",
        )

        self.assertEqual(headers["Access-Key"], "31c2602c-56a8-49e0-9709-746d8b2c8bd9")
        self.assertEqual(headers["X-Bce-Request-ID"], "19005119-88cf-11e8-8227-90e2ba1fdc4b")
        self.assertEqual(headers["Sign-Time"], "2022-05-10 18:13:14")
        self.assertEqual(headers["Token"], "a5de8a91c0c7db8ec2a1fd70c32766e350e6098a6d567e197665497c02bad99c")

    def test_non_json_body_is_not_signed(self) -> None:
        headers_with_body = build_ab_token_headers(
            access_key="ak",
            secret_key="sk",
            content_type="multipart/form-data",
            body="ignored",
            request_id="rid",
            sign_time="2026-05-20 10:00:00",
        )
        headers_without_body = build_ab_token_headers(
            access_key="ak",
            secret_key="sk",
            content_type="multipart/form-data",
            request_id="rid",
            sign_time="2026-05-20 10:00:00",
        )

        self.assertEqual(headers_with_body["Token"], headers_without_body["Token"])

    def test_fixed_request_id_and_sign_time_are_stable(self) -> None:
        first = build_ab_token_headers(
            access_key="ak",
            secret_key="sk",
            content_type="application/json",
            body={"a": 1},
            request_id="rid",
            sign_time="2026-05-20 10:00:00",
        )
        second = build_ab_token_headers(
            access_key="ak",
            secret_key="sk",
            content_type="application/json",
            body={"a": 1},
            request_id="rid",
            sign_time="2026-05-20 10:00:00",
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
