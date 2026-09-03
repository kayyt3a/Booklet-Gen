"""The practice grind engine: verified ATAR questions served one at a time.

Deliberately separate from the booklet pipeline. A booklet is generated for one
customer and printed once; a practice question is generated once and served to
every student who picks its subtopic, which is why nothing here runs a language
model in a request.
"""
