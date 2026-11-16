import unittest

from core.memory.intent import MemoryIntentDetector, MemoryIntentType


class TestPersonalMemoryRecall(unittest.TestCase):

    def setUp(self):
        self.detector = MemoryIntentDetector()

    def test_personal_name_questions(self):
        questions = [
            "What is my name?",
            "What's my name?",
            "What is my full name?",
            "Who am I?",
            "Tell me my name",
            "Do you know my name?",
        ]

        for question in questions:
            with self.subTest(question=question):
                intent = self.detector.detect(question)

                self.assertEqual(intent.type, MemoryIntentType.RECALL)
                self.assertEqual(intent.category, "personal")
                self.assertEqual(intent.key, "my_name")


if __name__ == "__main__":
    unittest.main(verbosity=2)
