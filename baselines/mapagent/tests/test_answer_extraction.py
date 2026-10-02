import unittest

from answer_extraction import extract_answer_choice


class AnswerExtractionTest(unittest.TestCase):
    choices = ["One", "Two", "Three", "Four"]

    def test_original_format(self):
        self.assertEqual(extract_answer_choice("The answer is B.", self.choices), "Two")

    def test_markdown_and_parenthesis(self):
        self.assertEqual(extract_answer_choice("Therefore, the answer is **B) Two**.", self.choices), "Two")
        self.assertEqual(extract_answer_choice("So, the answer is (B) Two.", self.choices), "Two")

    def test_uses_final_explicit_answer(self):
        self.assertEqual(extract_answer_choice("The answer is A. On reflection, the answer is C.", self.choices), "Three")

    def test_does_not_guess_when_no_choice_is_stated(self):
        self.assertIsNone(extract_answer_choice("The answer is unavailable.", self.choices))
        self.assertIsNone(extract_answer_choice("The answer is D.", self.choices[:2]))


if __name__ == "__main__":
    unittest.main()
