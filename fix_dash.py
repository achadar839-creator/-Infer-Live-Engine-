with open("frontend/dashboard.html", "r", encoding="utf-8") as f:
    c = f.read()

# जो बैकस्लैश (\) पूरे बटनों को जाम कर रहा है, उसे हटा रहे हैं
c = c.replace('\\"string\\"', '"string"')

with open("frontend/dashboard.html", "w", encoding="utf-8") as f:
    f.write(c)
