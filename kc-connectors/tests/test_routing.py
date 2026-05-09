from kc_connectors.routing import RoutingTable


def test_default_route_to_main_agent():
    rt = RoutingTable(default_agent="KonaClaw")
    assert rt.route(channel="telegram", chat_id="42") == "KonaClaw"


def test_specific_route_overrides():
    rt = RoutingTable(default_agent="KonaClaw")
    rt.set_route(channel="telegram", chat_id="42", agent="ResearchBot")
    assert rt.route(channel="telegram", chat_id="42") == "ResearchBot"
    assert rt.route(channel="telegram", chat_id="99") == "KonaClaw"


def test_yaml_round_trip(tmp_path):
    p = tmp_path / "routes.yaml"
    rt = RoutingTable(default_agent="KonaClaw")
    rt.set_route(channel="telegram", chat_id="42", agent="ResearchBot")
    rt.save_to_yaml(p)
    rt2 = RoutingTable.load_from_yaml(p)
    assert rt2.route("telegram", "42") == "ResearchBot"
