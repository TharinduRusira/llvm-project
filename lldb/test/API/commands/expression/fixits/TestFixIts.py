"""
Test calling an expression with errors that a FixIt can fix.
"""

import lldb
from lldbsuite.test.decorators import *
from lldbsuite.test.lldbtest import *
from lldbsuite.test import lldbutil


class ExprCommandWithFixits(TestBase):

    mydir = TestBase.compute_mydir(__file__)

    def test_with_dummy_target(self):
        """Test calling expressions in the dummy target with errors that can be fixed by the FixIts."""

        # Enable fix-its as they were intentionally disabled by TestBase.setUp.
        self.runCmd("settings set target.auto-apply-fixits true")

        ret_val = lldb.SBCommandReturnObject()
        result = self.dbg.GetCommandInterpreter().HandleCommand("expression ((1 << 16) - 1))", ret_val)
        self.assertEqual(result, lldb.eReturnStatusSuccessFinishResult, "The expression was successful.")
        self.assertTrue("Fix-it applied" in ret_val.GetError(), "Found the applied FixIt.")

    def test_with_target(self):
        """Test calling expressions with errors that can be fixed by the FixIts."""
        self.build()
        (target, process, self.thread, bkpt) = lldbutil.run_to_source_breakpoint(self,
                                        'Stop here to evaluate expressions',
                                         lldb.SBFileSpec("main.cpp"))

        options = lldb.SBExpressionOptions()
        options.SetAutoApplyFixIts(True)

        top_level_options = lldb.SBExpressionOptions()
        top_level_options.SetAutoApplyFixIts(True)
        top_level_options.SetTopLevel(True)

        frame = self.thread.GetFrameAtIndex(0)

        # Try with one error:
        value = frame.EvaluateExpression("my_pointer.first", options)
        self.assertTrue(value.IsValid())
        self.assertTrue(value.GetError().Success())
        self.assertEquals(value.GetValueAsUnsigned(), 10)

        # Try with one error in a top-level expression.
        # The Fix-It changes "ptr.m" to "ptr->m".
        expr = "struct X { int m; }; X x; X *ptr = &x; int m = ptr.m;"
        value = frame.EvaluateExpression(expr, top_level_options)
        # A successfully parsed top-level expression will yield an error
        # that there is 'no value'. If a parsing error would have happened we
        # would get a different error kind, so let's check the error kind here.
        self.assertEquals(value.GetError().GetCString(), "error: No value")

        # Try with two errors:
        two_error_expression = "my_pointer.second->a"
        value = frame.EvaluateExpression(two_error_expression, options)
        self.assertTrue(value.IsValid())
        self.assertTrue(value.GetError().Success())
        self.assertEquals(value.GetValueAsUnsigned(), 20)

        # Try a Fix-It that is stored in the 'note:' diagnostic of an error.
        # The Fix-It here is adding parantheses around the ToStr parameters.
        fixit_in_note_expr ="#define ToStr(x) #x\nToStr(0 {, })"
        value = frame.EvaluateExpression(fixit_in_note_expr, options)
        self.assertTrue(value.IsValid())
        self.assertTrue(value.GetError().Success(), value.GetError())
        self.assertEquals(value.GetSummary(), '"(0 {, })"')

        # Now turn off the fixits, and the expression should fail:
        options.SetAutoApplyFixIts(False)
        value = frame.EvaluateExpression(two_error_expression, options)
        self.assertTrue(value.IsValid())
        self.assertTrue(value.GetError().Fail())
        error_string = value.GetError().GetCString()
        self.assertTrue(
            error_string.find("fixed expression suggested:") != -1,
            "Fix was suggested")
        self.assertTrue(
            error_string.find("my_pointer->second.a") != -1,
            "Fix was right")


        # Test repeatedly applying Fix-Its to expressions and reparsing them.
        multiple_runs_options = lldb.SBExpressionOptions()
        multiple_runs_options.SetAutoApplyFixIts(True)
        multiple_runs_options.SetTopLevel(True)

        # An expression that needs two parse attempts with one Fix-It each
        # to be successfully parsed.
        two_runs_expr = """
        struct Data { int m; };

        template<typename T>
        struct S1 : public T {
          using T::TypeDef;
          int f() {
            Data d;
            d.m = 123;
            // The first error as the using above requires a 'typename '.
            // Will trigger a Fix-It that puts 'typename' in the right place.
            typename S1<T>::TypeDef i = &d;
            // i has the type "Data *", so this should be i.m.
            // The second run will change the . to -> via the Fix-It.
            return i.m;
          }
        };

        struct ClassWithTypeDef {
          typedef Data *TypeDef;
        };

        int test_X(int i) {
          S1<ClassWithTypeDef> s1;
          return s1.f();
        }
        """

        # Disable retries which will fail.
        multiple_runs_options.SetRetriesWithFixIts(0)
        value = frame.EvaluateExpression(two_runs_expr, multiple_runs_options)
        self.assertIn("expression failed to parse, fixed expression suggested:",
                      value.GetError().GetCString())
        self.assertIn("using typename T::TypeDef",
                      value.GetError().GetCString())
        # The second Fix-It shouldn't be suggested here as Clang should have
        # aborted the parsing process.
        self.assertNotIn("i->m",
                      value.GetError().GetCString())

        # Retry once, but the expression needs two retries.
        multiple_runs_options.SetRetriesWithFixIts(1)
        value = frame.EvaluateExpression(two_runs_expr, multiple_runs_options)
        self.assertIn("expression failed to parse, fixed expression suggested:",
                      value.GetError().GetCString())
        # Both our fixed expressions should be in the suggested expression.
        self.assertIn("using typename T::TypeDef",
                      value.GetError().GetCString())
        self.assertIn("i->m",
                      value.GetError().GetCString())

        # Retry twice, which will get the expression working.
        multiple_runs_options.SetRetriesWithFixIts(2)
        value = frame.EvaluateExpression(two_runs_expr, multiple_runs_options)
        # This error signals success for top level expressions.
        self.assertEquals(value.GetError().GetCString(), "error: No value")

        # Test that the code above compiles to the right thing.
        self.expect_expr("test_X(1)", result_type="int", result_value="123")
