"""MCP server entrypoint: registers the rules-lookup and character-building
tool layers onto a single MCPServer sharing one SQLite database."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import (
    build_tools,
    character_tools,
    chronicle_sheet,
    pfs_tools,
    rules_tools,
    sheet,
)

mcp = MCPServer(
    name="pf2e-mcp",
    instructions=(
        "Pathfinder 2e Remastered rules reference and collaborative character "
        "building. Prefer these tools over recalled rules knowledge -- content "
        "changes weekly via errata, and prerequisite/derived-stat calculations "
        "here are authoritative where model recall is not."
    ),
)

mcp.add_tool(rules_tools.rules_search, name="rules_search")
mcp.add_tool(rules_tools.rules_get_entry, name="rules_get_entry")
mcp.add_tool(rules_tools.rules_related, name="rules_related")
mcp.add_tool(rules_tools.rules_explain, name="rules_explain")
mcp.add_tool(rules_tools.rules_data_version, name="rules_data_version")
mcp.add_tool(rules_tools.list_variant_rules, name="rules_list_variant_rules")
mcp.add_tool(rules_tools.list_subclass_option_groups, name="rules_list_subclass_option_groups")
mcp.add_tool(rules_tools.list_subclass_options, name="rules_list_subclass_options")
mcp.add_tool(rules_tools.rules_search_aon, name="rules_search_aon")

mcp.add_tool(build_tools.list_ancestries, name="build_list_ancestries")
mcp.add_tool(build_tools.list_backgrounds, name="build_list_backgrounds")
mcp.add_tool(build_tools.list_classes, name="build_list_classes")
mcp.add_tool(build_tools.list_equipment, name="build_list_equipment")
mcp.add_tool(build_tools.list_available_feats, name="build_list_available_feats")
mcp.add_tool(build_tools.list_ability_boost_options, name="build_list_ability_boost_options")
mcp.add_tool(build_tools.list_skill_increase_options, name="build_list_skill_increase_options")
mcp.add_tool(build_tools.check_prerequisite, name="build_check_prerequisite")
mcp.add_tool(build_tools.validate_build, name="build_validate_build")
mcp.add_tool(build_tools.calculate_derived_stats, name="build_calculate_derived_stats")
mcp.add_tool(build_tools.list_available_spells, name="build_list_available_spells")
mcp.add_tool(build_tools.get_level_up_choices, name="build_get_level_up_choices")
mcp.add_tool(build_tools.to_pathbuilder_export, name="build_to_pathbuilder_export")

mcp.add_tool(character_tools.character_schema, name="build_character_schema")
mcp.add_tool(character_tools.validate_character, name="build_validate_character")
mcp.add_tool(character_tools.character_at_level, name="build_character_at_level")

mcp.add_tool(pfs_tools.get_adventure, name="pfs_get_adventure")
mcp.add_tool(pfs_tools.find_adventures, name="pfs_find_adventures")
mcp.add_tool(pfs_tools.chronicle_schema, name="pfs_chronicle_schema")
mcp.add_tool(pfs_tools.validate_chronicle, name="pfs_validate_chronicle")
mcp.add_tool(pfs_tools.earn_income, name="pfs_earn_income")

# The two tools here that write to disk rather than only reading the database:
# a rendered sheet is far too large to return through a tool response.
mcp.add_tool(sheet.render_character_sheet, name="build_render_character_sheet")
mcp.add_tool(chronicle_sheet.render_chronicle_sheet, name="pfs_render_chronicle_sheet")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
