import json
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import tool
class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs): return self
@tool
def market_data(symbol:str):
    """Fixture quote provider."""
    return json.dumps({"symbol":symbol,"price":0.51,"currency":"MYR"})
