from booklet_gen.agents.validator import SympyValidator
from booklet_gen.schemas import Question

v = SympyValidator()

cases = [
    # (question, answer, expected_verified)  expected None = should defer to judge
    ("Differentiate y = x^3 + 2x with respect to x.", "dy/dx = 3x^2 + 2", True),
    ("Differentiate y = x^3 + 2x with respect to x.", "dy/dx = 3x^2 + 1", False),
    ("Find the derivative of f(x) = sin(2x).", "f'(x) = 2cos(2x)", True),
    ("Find the derivative of f(x) = sin(2x).", "f'(x) = cos(2x)", False),
    ("Given f(x) = x^2 e^x, find the derivative.", "f'(x) = 2x e^x + x^2 e^x", True),
    ("Differentiate y = ln(3x).", "dy/dx = 1/x", True),
    ("Differentiate y = (2x + 1)^4.", "dy/dx = 8(2x+1)^3", True),
    ("If f(x) = x^3 - 4x, find f'(2).", "8", True),
    ("If f(x) = x^3 - 4x, find f'(2).", "10", False),
    # integrals
    ("Integrate 3x^2 + 1 with respect to x.", "x^3 + x + c", True),
    ("Integrate 3x^2 + 1 with respect to x.", "x^3 + 2x + c", False),
    ("Find the antiderivative of cos(2x) dx.", "(1/2)sin(2x) + c", True),
    ("Evaluate the integral of x^2 dx from 0 to 2.", "8/3", True),
    ("Evaluate the integral of x^2 dx from 0 to 2.", "4", False),
    ("Evaluate the integral of e^x dx from 0 to 1.", "e - 1", True),
    # non-calculus / prose: must defer, not falsely reject
    ("Explain why the derivative represents a gradient.",
     "It is the instantaneous rate of change.", None),
    ("A bag has 3 red and 5 blue balls. Find P(red).", "3/8", None),
]

ok = 0
for q, a, exp in cases:
    r = v.validate(Question(question=q, answer=a, working=""))
    got = r.verified
    notes = r.notes or ""
    if exp is None:
        # Deferring looks like: not verified, and the note says it could not
        # find a verifiable pattern (so the pipeline hands it to the LLM judge).
        good = (not got) and ("no symbolically" in notes or "no matching" in notes)
        status = "DEFER-OK" if good else "*** LEAKED ***"
    else:
        good = got == exp
        status = "PASS" if good else "*** FAIL ***"
    ok += bool(good)
    print(f"{status:<15} {q[:44]:<46} ans={a[:20]:<22} -> verified={got}")
    print(f"{'':<15} {notes[:100]}")

print(f"\n{ok}/{len(cases)} behaved as expected")
