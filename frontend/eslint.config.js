import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  {
    // Global ignores — must be its own object to apply repo-wide.
    ignores: ["dist", "node_modules", "coverage"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      // Non type-checked recommended set: keeps lint fast.
      // Type correctness is enforced separately by `npm run typecheck` (tsc --noEmit).
      ...tseslint.configs.recommended,
    ],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      // Rules of Hooks violations are real bugs — fail the build.
      "react-hooks/rules-of-hooks": "error",
      // Missing/extra effect deps are often intentional; surface as warnings
      // for human review rather than auto-suppressing or blocking CI.
      "react-hooks/exhaustive-deps": "warn",

      // Allow intentionally-unused identifiers when prefixed with `_`.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],

      // Security review decision: LLM/chat content is untrusted and must never be
      // injected as raw HTML. dangerouslySetInnerHTML bypasses React's escaping and
      // is an XSS vector for model/user-generated text. Render as text, or sanitize
      // through a vetted sanitizer in a dedicated, reviewed utility if ever required.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML is forbidden: LLM/chat content must never be rendered as HTML without sanitization (security review decision). Render as text instead.",
        },
      ],
    },
  },
  {
    // Tests exercise mocks, partial fixtures and intentionally-loose shapes;
    // explicit `any` there is pragmatic and not worth a flood of errors. Type
    // safety of production code is unaffected (this scope is tests only).
    files: ["src/**/*.test.{ts,tsx}", "src/testSetup.ts"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);
