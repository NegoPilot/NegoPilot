"""Keep service entity fields aligned with the independently deployed business MCP."""
import importlib.util
from pathlib import Path
from negopilot_shared.decomposition import CompanySizeFacts


def test_service_and_mcp_size_input_fields_match():
    path=Path(__file__).resolve().parents[2]/'mcp-servers/business-mcp/company_size.py'
    spec=importlib.util.spec_from_file_location('size_contract',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert CompanySizeFacts.model_json_schema()['properties']==module.CompanySizeInput.model_json_schema()['properties']
