import argparse
import copy
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional


def build_output_filename(input_file: Path, suffix: str = "topology") -> Path:
    """Build output filename with timestamp."""
    timestamp = int(time.time())
    input_stem = input_file.stem
    filename = f"{input_stem}_{suffix}_{timestamp}.json"
    return Path.cwd() / filename


def extract_entities_from_json(data: Any) -> List[Dict[str, Any]]:
    """Extract entities from JSON, handling both v1 (array) and v2 (dict with entities key) formats."""
    if isinstance(data, list):
        # V1 format: direct array
        return data
    elif isinstance(data, dict):
        # V2 format: dict with entities key
        return data.get("entities", [])
    else:
        return []


def clean_unsupported_metadata(component: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert unsupported data types to strings (matches dynatrace_topology.py v2 implementation).
    Works on a copy to avoid modifying the original.
    """
    # Work on a copy to avoid modifying the original
    component = copy.deepcopy(component)
    # Convert float, bool, int to string (top-level only, not recursive for performance)
    for key in list(component.keys()):
        value = component[key]
        if isinstance(value, float):
            component[key] = str(value)
        elif isinstance(value, bool):
            component[key] = str(value)
        elif isinstance(value, int):
            component[key] = str(value)
    
    # Handle nested properties (matches v2 implementation)
    if "properties" in component and isinstance(component["properties"], dict):
        properties = component["properties"]
        
        # Handle releasesVersion field - convert string representation to empty dict if it's a string
        if "releasesVersion" in properties and isinstance(properties["releasesVersion"], str):
            properties["releasesVersion"] = {}
        
        # Handle osServices field - convert dict format to list of service names
        if "osServices" in properties:
            if isinstance(properties["osServices"], list):
                converted_services = []
                for i, service in enumerate(properties["osServices"]):
                    if isinstance(service, dict):
                        service_name = (
                            service.get('dt.osservice.name') or 
                            service.get('dt.osservice.display_name') or 
                            f'unknown_service_{i}'
                        )
                        converted_services.append(service_name)
                    elif isinstance(service, str):
                        converted_services.append(service)
                    else:
                        converted_services.append(str(service))
                properties["osServices"] = converted_services
        
        # Handle customPgMetadata field - convert list of key-value objects to dictionary
        if "customPgMetadata" in properties:
            if isinstance(properties["customPgMetadata"], list):
                converted_dict = {}
                for i, item in enumerate(properties["customPgMetadata"]):
                    if isinstance(item, dict):
                        raw_key = item.get('key')
                        # Handle nested key structure
                        if isinstance(raw_key, dict):
                            nested_key = raw_key.get('key')
                            if isinstance(nested_key, (str, int, float, bool)):
                                key = str(nested_key)
                            else:
                                key = f'unknown_key_{i}'
                        elif isinstance(raw_key, (str, int, float, bool)):
                            key = str(raw_key)
                        else:
                            key = f'unknown_key_{i}'
                        value = item.get('value', item.get('val', f'unknown_value_{i}'))
                        converted_dict[key] = value
                    else:
                        converted_dict[f'item_{i}'] = str(item)
                properties["customPgMetadata"] = converted_dict
            elif not isinstance(properties["customPgMetadata"], dict):
                properties["customPgMetadata"] = {}
        
        # Handle logFileStatus field - wrap list in expected structure
        if "logFileStatus" in properties:
            if isinstance(properties["logFileStatus"], list):
                properties["logFileStatus"] = {"logFileStatus": properties["logFileStatus"]}
            elif not isinstance(properties["logFileStatus"], dict):
                properties["logFileStatus"] = None
        
        # Handle logSourceState field - wrap list in expected structure
        if "logSourceState" in properties:
            if isinstance(properties["logSourceState"], list):
                properties["logSourceState"] = {"logSourceState": properties["logSourceState"]}
            elif not isinstance(properties["logSourceState"], dict):
                properties["logSourceState"] = None
    
    # Remove lastSeenTimestamp if present
    if "lastSeenTimestamp" in component:
        del component["lastSeenTimestamp"]
    
    return component


def create_component_identifier(entity_id: str) -> str:
    """Create identifier for component (matches Identifiers.create_custom_identifier format)."""
    return f"urn:dynatrace:/{entity_id}"


def extract_tags(entity: Dict[str, Any]) -> List[str]:
    """Extract tags as labels from entity."""
    tags = []
    entity_tags = entity.get("tags", [])
    
    for tag in entity_tags:
        if not isinstance(tag, dict):
            continue
        
        tag_label = ""
        context = tag.get("context")
        if context and context != "CONTEXTLESS":
            tag_label += f"[{context}]"
        
        key = tag.get("key")
        if key:
            tag_label += key
        
        value = tag.get("value")
        if value:
            tag_label += f":{value}"
        
        if tag_label:
            tags.append(tag_label)
    
    return tags


def extract_management_zones(entity: Dict[str, Any]) -> List[str]:
    """Extract management zone labels."""
    zones = []
    management_zones = entity.get("managementZones", [])
    
    for zone in management_zones:
        if isinstance(zone, dict):
            zone_name = zone.get("name")
            if zone_name:
                zones.append(f"managementZones:{zone_name}")
    
    return zones


def normalize_process_group_v2_to_v1(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Dynatrace Entities API v2 process-group element shape into the Smartscape v1-like shape.
    Matches dynatrace_topology.py _normalize_process_group_v2_to_v1 implementation.
    """
    properties = data.get("properties") or {}
    if not isinstance(properties, dict):
        return data
    
    # 1) Move listenPorts to top-level
    listen_ports = properties.pop("listenPorts", None)
    if listen_ports and isinstance(listen_ports, list):
        data["listenPorts"] = listen_ports
    
    # 2) Move softwareTechnologies to top-level
    software_techs = properties.pop("softwareTechnologies", None)
    if software_techs and isinstance(software_techs, list):
        data["softwareTechnologies"] = software_techs
    
    # 3) Map detectedName -> discoveredName if missing
    detected_name = properties.get("detectedName")
    if detected_name and not data.get("discoveredName"):
        data["discoveredName"] = detected_name
    
    # 4) Convert metadata entries list -> dict-of-arrays with v1 keys
    metadata_entries = properties.pop("metadata", None)
    if metadata_entries and isinstance(metadata_entries, list):
        key_mapping = {
            "COMMAND_LINE_ARGS": "commandLineArgs",
            "EXE_NAME": "executables",
            "EXE_PATH": "executablePaths",
            "JAVA_MAIN_CLASS": "javaMainClasses",
            "CONTAINER_IMAGE_NAME": "containerImageNames",
            "CONTAINER_IMAGE_VERSION": "containerImageVersions",
            "CONTAINER_NAME": "containerNames",
            "ELASTIC_SEARCH_CLUSTER_NAMES": "elasticSearchClusterNames",
            "ELASTIC_SEARCH_NODE_NAMES": "elasticSearchNodeNames",
            "PG_ID_CALC_INPUT_KEY_LINKAGE": "pgIdCalcInputKeyLinkage",
            "JAVA_JAR_FILE": "javaJarFiles",
            "JAVA_JAR_PATH": "javaJarPaths",
        }
        meta_out: Dict[str, List[Any]] = {}
        for entry in metadata_entries:
            if not isinstance(entry, dict):
                continue
            raw_key = entry.get("key")
            value = entry.get("value")
            if not raw_key:
                continue
            target_key = key_mapping.get(raw_key)
            if not target_key:
                target_key = raw_key
            if target_key not in meta_out:
                meta_out[target_key] = []
            if value is not None:
                meta_out[target_key].append(value)
        if meta_out:
            data["metadata"] = meta_out
    
    return data


def process_entity_to_component(entity: Dict[str, Any], component_type: str) -> Dict[str, Any]:
    """
    Process a single entity into a topology component format.
    Similar to _collect_topology in dynatrace_topology.py.
    """
    # Clean the entity data
    cleaned_entity = clean_unsupported_metadata(entity)
    
    # Extract basic information
    entity_id = cleaned_entity.get("entityId", "")
    display_name = cleaned_entity.get("displayName", entity_id)
    
    # Create identifiers
    identifiers = [create_component_identifier(entity_id)]
    
    # Extract tags and labels
    tags = extract_tags(cleaned_entity)
    management_zones = extract_management_zones(cleaned_entity)
    tags.extend(management_zones)
    
    # Add entity ID as a tag
    if entity_id:
        tags.append(entity_id)
    
    # Extract software technologies if present
    software_techs = cleaned_entity.get("softwareTechnologies", [])
    if software_techs:
        for tech in software_techs:
            if isinstance(tech, dict):
                tech_parts = [
                    tech.get("type"),
                    tech.get("edition"),
                    tech.get("version"),
                ]
                tech_label = ":".join(filter(None, tech_parts))
                if tech_label:
                    tags.append(tech_label)
    
    # Extract monitoring state if present
    monitoring_state = cleaned_entity.get("monitoringState")
    if isinstance(monitoring_state, dict):
        actual_state = monitoring_state.get("actualMonitoringState")
        expected_state = monitoring_state.get("expectedMonitoringState")
        if actual_state:
            tags.append(f"actualMonitoringState:{actual_state}")
        if expected_state:
            tags.append(f"expectedMonitoringState:{expected_state}")
    
    # Build component data (matches _collect_topology pattern)
    component_data = {}
    component_data.update(cleaned_entity)
    
    # Remove relationship data and tags from component (matches _filter_item_topology_data)
    component_data.pop("fromRelationships", None)
    component_data.pop("toRelationships", None)
    component_data.pop("tags", None)
    
    # Normalize API v2 process-group payloads to v1-style shape (matches v2 implementation)
    if component_type == "process-group":
        component_data = normalize_process_group_v2_to_v1(component_data)
    
    # Add topology metadata
    component_data.update({
        "identifiers": identifiers,
        "tags": tags,
        "component_type": component_type,
    })
    
    return {
        "entityId": entity_id,
        "displayName": display_name,
        "component_type": component_type,
        "component": component_data,
        "fromRelationships": cleaned_entity.get("fromRelationships", {}),
        "toRelationships": cleaned_entity.get("toRelationships", {}),
    }


def process_topology(input_file: Path, component_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Process JSON file into topology format.
    """
    print(f"Reading input file: {input_file}")
    
    # Read and parse JSON
    with open(input_file, "r") as f:
        data = json.load(f)
    
    # Extract entities
    entities = extract_entities_from_json(data)
    print(f"Found {len(entities)} entities in input file")
    
    # Determine component type from filename if not provided
    if not component_type:
        filename = input_file.name.lower()
        if "process" in filename and "group" not in filename:
            component_type = "process"
        elif "process-group" in filename:
            component_type = "process-group"
        else:
            component_type = "entity"
    
    # Process each entity
    components = []
    relationships = []
    
    for entity in entities:
        try:
            processed = process_entity_to_component(entity, component_type)
            components.append(processed["component"])
            
            # Collect relationships
            entity_id = processed["entityId"]
            from_rels = processed["fromRelationships"]
            to_rels = processed["toRelationships"]
            
            # Process fromRelationships (outgoing)
            for rel_type, rel_targets in from_rels.items():
                if not isinstance(rel_targets, list):
                    continue
                for target in rel_targets:
                    target_id = target.get("id") if isinstance(target, dict) else target
                    if target_id:
                        relationships.append({
                            "source": entity_id,
                            "target": target_id,
                            "type": rel_type,
                        })
            
            # Process toRelationships (incoming)
            for rel_type, rel_sources in to_rels.items():
                if not isinstance(rel_sources, list):
                    continue
                for source in rel_sources:
                    source_id = source.get("id") if isinstance(source, dict) else source
                    if source_id:
                        relationships.append({
                            "source": source_id,
                            "target": entity_id,
                            "type": rel_type,
                        })
        except Exception as e:
            entity_id = entity.get("entityId", "UNKNOWN")
            print(f"Warning: Failed to process entity {entity_id}: {e}")
            continue
    
    print(f"Processed {len(components)} components")
    print(f"Extracted {len(relationships)} relationships")
    
    # Build topology structure
    topology = {
        "metadata": {
            "source_file": str(input_file),
            "component_type": component_type,
            "timestamp": int(time.time()),
            "component_count": len(components),
            "relationship_count": len(relationships),
        },
        "components": components,
        "relationships": relationships,
    }
    
    return topology


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process Dynatrace JSON response files into topology format."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to input JSON file (v1 or v2 format)",
    )
    parser.add_argument(
        "--component-type",
        type=str,
        choices=["process", "process-group", "entity"],
        help="Component type to use (auto-detected from filename if not provided)",
    )
    parser.add_argument(
        "--output-suffix",
        type=str,
        default="topology",
        help="Suffix for output filename (default: topology)",
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not args.input_file.exists():
        print(f"Error: Input file not found: {args.input_file}")
        return
    
    # Process topology
    topology = process_topology(args.input_file, args.component_type)
    
    # Write output
    output_file = build_output_filename(args.input_file, args.output_suffix)
    with open(output_file, "w") as f:
        json.dump(topology, f, indent=2)
    
    print(f"Wrote topology to {output_file}")
    print(f"  Components: {topology['metadata']['component_count']}")
    print(f"  Relationships: {topology['metadata']['relationship_count']}")


if __name__ == "__main__":
    main()

