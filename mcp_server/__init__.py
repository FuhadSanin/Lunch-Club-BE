"""The Lunch Club MCP server: lets an AI assistant run a group lunch for one person.

It is a thin layer over the REST API (apps/): every tool is one or two API calls made
as the signed-in person (the token their assistant got on Allow), so it can do exactly
what that person can do in the web app, and no more (the API checks, not this file).
"""
