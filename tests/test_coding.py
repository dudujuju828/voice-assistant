from __future__ import annotations

import unittest

import coding


class CodingIntentTests(unittest.TestCase):
    def test_coding_requests_are_detected(self) -> None:
        for text in [
            "edit the cpp file",
            "edit my config file",
            "fix the bug in the parser",
            "refactor the auth module",
            "add a function that sorts the list",
            "write a function to parse the header",
            "rename the helper in the project",
            "update the code in the repo",
            "implement the retry logic",
            "create a new file for the router",
            "change the function that loads settings",
            "modify the build script",
            "comment out the debug logging",
            "open the python file and add a class",
        ]:
            self.assertTrue(coding.looks_like_coding_request(text), msg=text)

    def test_plain_turns_are_not_coding_requests(self) -> None:
        for text in [
            "what time is it",
            "tell me a joke",
            "summarize this screen",
            "what does this error mean",
            "open the window",
            "fix my schedule for tomorrow",
            "search the web for the weather",
        ]:
            self.assertFalse(coding.looks_like_coding_request(text), msg=text)

    def test_detection_is_case_insensitive(self) -> None:
        self.assertTrue(coding.looks_like_coding_request("EDIT THE CPP FILE"))

    def test_empty_text_is_not_a_coding_request(self) -> None:
        self.assertFalse(coding.looks_like_coding_request(""))
        self.assertFalse(coding.looks_like_coding_request(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
