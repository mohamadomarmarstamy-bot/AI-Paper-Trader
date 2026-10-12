import ast
import unittest
from pathlib import Path


class PendingEntrySkipTests(unittest.TestCase):

    def test_pending_entry_uses_cycle_result(self):
        path = Path(__file__).with_name("main.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))

        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_auto_trader_cycle"
        ]

        self.assertEqual(len(functions), 1)

        function = functions[0]

        pending_checks = [
            node for node in ast.walk(function)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and ast.unparse(node.test)
            == "symbol in pending_entry_symbols"
        ]

        self.assertEqual(len(pending_checks), 1)

        branch = pending_checks[0]

        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Subscript)
                and ast.unparse(node.func.value)
                == "cycle_result['skipped_candidates']"
                for statement in branch.body
                for node in ast.walk(statement)
            ),
            "Pending entry must be recorded in cycle_result",
        )


if __name__ == "__main__":
    unittest.main()
