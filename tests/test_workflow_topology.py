from workflow import create_bursa_agent_graph


def test_workflow_has_explicit_debate_before_judge():
    graph = create_bursa_agent_graph()
    graph_text = graph.get_graph().draw_mermaid()

    assert "bull_agent --> debate_agent" in graph_text
    assert "bear_agent --> debate_agent" in graph_text
    assert "debate_agent --> judge_agent" in graph_text
