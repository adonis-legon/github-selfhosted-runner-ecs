"""
Validate CloudFormation template for task 6.2 requirements:
- ECS Cluster with Fargate capacity provider
- x86_64 Task Definition with runtimePlatform X86_64/LINUX
- arm64 Task Definition with runtimePlatform ARM64/LINUX
- CPU (default 2048), memory (default 4096), ephemeral storage (default 30, min 21)
- Container definitions with environment variables, secrets, and CloudWatch Logs
- Template parameters: CpuAllocation, MemoryAllocation, EphemeralStorage
- No persistent volumes (Requirement 8.3)
"""

import sys
import yaml


# ============================================================
# Custom YAML loader that handles CloudFormation intrinsic tags
# ============================================================

class CloudFormationLoader(yaml.SafeLoader):
    """YAML loader that supports CloudFormation intrinsic function tags."""
    pass


def _construct_cfn_tag(loader, tag_suffix, node):
    """Generic constructor for CloudFormation tags that preserves structure."""
    if isinstance(node, yaml.ScalarNode):
        return {"Fn::Tag": tag_suffix, "Value": loader.construct_scalar(node)}
    elif isinstance(node, yaml.SequenceNode):
        return {"Fn::Tag": tag_suffix, "Value": loader.construct_sequence(node)}
    elif isinstance(node, yaml.MappingNode):
        return {"Fn::Tag": tag_suffix, "Value": loader.construct_mapping(node)}
    return None


# Register constructors for all CloudFormation intrinsic functions
_cfn_tags = [
    "!Ref", "!Sub", "!GetAtt", "!Select", "!GetAZs",
    "!Join", "!Split", "!If", "!Equals", "!And", "!Or", "!Not",
    "!FindInMap", "!Base64", "!Cidr", "!ImportValue",
    "!Condition", "!Transform",
]

for tag in _cfn_tags:
    CloudFormationLoader.add_constructor(
        tag,
        lambda loader, node, t=tag: _construct_cfn_tag(loader, t, node)
    )

# Also handle multi-constructor for any unknown tags
CloudFormationLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: _construct_cfn_tag(loader, "!" + suffix, node)
)


def load_template(path):
    """Load CloudFormation template with intrinsic function support."""
    with open(path, "r") as f:
        return yaml.load(f, Loader=CloudFormationLoader)


# ============================================================
# Validation functions
# ============================================================

def validate_parameters(template):
    """Validate template parameters for task 6.2."""
    params = template.get("Parameters", {})
    errors = []

    # CpuAllocation parameter
    if "CpuAllocation" not in params:
        errors.append("Missing parameter: CpuAllocation")
    else:
        cpu = params["CpuAllocation"]
        if cpu.get("Default") != "2048":
            errors.append(f"CpuAllocation default should be '2048', got '{cpu.get('Default')}'")

    # MemoryAllocation parameter
    if "MemoryAllocation" not in params:
        errors.append("Missing parameter: MemoryAllocation")
    else:
        mem = params["MemoryAllocation"]
        if mem.get("Default") != "4096":
            errors.append(f"MemoryAllocation default should be '4096', got '{mem.get('Default')}'")

    # EphemeralStorage parameter
    if "EphemeralStorage" not in params:
        errors.append("Missing parameter: EphemeralStorage")
    else:
        eph = params["EphemeralStorage"]
        if eph.get("Default") != 30:
            errors.append(f"EphemeralStorage default should be 30, got {eph.get('Default')}")
        if eph.get("MinValue") is None or eph.get("MinValue") > 21:
            errors.append(f"EphemeralStorage MinValue should be <= 21, got {eph.get('MinValue')}")

    return errors


def validate_ecs_cluster(template):
    """Validate ECS Cluster with Fargate capacity provider."""
    resources = template.get("Resources", {})
    errors = []

    # Find ECS Cluster resource
    cluster = None
    for name, resource in resources.items():
        if resource.get("Type") == "AWS::ECS::Cluster":
            cluster = resource
            break

    if cluster is None:
        errors.append("Missing ECS Cluster resource (AWS::ECS::Cluster)")
        return errors

    props = cluster.get("Properties", {})
    capacity_providers = props.get("CapacityProviders", [])
    if "FARGATE" not in capacity_providers:
        errors.append("ECS Cluster must have FARGATE in CapacityProviders")

    return errors


def validate_task_definition(template, resource_name, expected_arch):
    """Validate a task definition resource."""
    resources = template.get("Resources", {})
    errors = []

    if resource_name not in resources:
        errors.append(f"Missing resource: {resource_name}")
        return errors

    td = resources[resource_name]
    if td.get("Type") != "AWS::ECS::TaskDefinition":
        errors.append(f"{resource_name} should be AWS::ECS::TaskDefinition, got {td.get('Type')}")
        return errors

    props = td.get("Properties", {})

    # Check RuntimePlatform
    runtime = props.get("RuntimePlatform", {})
    if runtime.get("CpuArchitecture") != expected_arch:
        errors.append(f"{resource_name} CpuArchitecture should be '{expected_arch}', got '{runtime.get('CpuArchitecture')}'")
    if runtime.get("OperatingSystemFamily") != "LINUX":
        errors.append(f"{resource_name} OperatingSystemFamily should be 'LINUX', got '{runtime.get('OperatingSystemFamily')}'")

    # Check RequiresCompatibilities
    compat = props.get("RequiresCompatibilities", [])
    if "FARGATE" not in compat:
        errors.append(f"{resource_name} must have FARGATE in RequiresCompatibilities")

    # Check NetworkMode
    if props.get("NetworkMode") != "awsvpc":
        errors.append(f"{resource_name} NetworkMode should be 'awsvpc'")

    # Check EphemeralStorage exists
    eph = props.get("EphemeralStorage", {})
    if not eph:
        errors.append(f"{resource_name} missing EphemeralStorage configuration")

    # Check container definitions
    containers = props.get("ContainerDefinitions", [])
    if not containers:
        errors.append(f"{resource_name} has no ContainerDefinitions")
        return errors

    container = containers[0]

    # Check environment variables
    env_vars = container.get("Environment", [])
    env_names = [e.get("Name") for e in env_vars]
    required_env = ["GITHUB_ORG", "RUNNER_LABELS", "PAT_SSM_PARAM", "AWS_REGION"]
    for var in required_env:
        if var not in env_names:
            errors.append(f"{resource_name} container missing environment variable: {var}")

    # Check secrets
    secrets = container.get("Secrets", [])
    secret_names = [s.get("Name") for s in secrets]
    if "GITHUB_PAT" not in secret_names:
        errors.append(f"{resource_name} container missing secret: GITHUB_PAT")

    # Check log configuration
    log_config = container.get("LogConfiguration", {})
    if log_config.get("LogDriver") != "awslogs":
        errors.append(f"{resource_name} container LogDriver should be 'awslogs', got '{log_config.get('LogDriver')}'")

    return errors


def validate_no_persistent_volumes(template):
    """Validate no persistent volumes are attached (Requirement 8.3)."""
    resources = template.get("Resources", {})
    errors = []

    for name, resource in resources.items():
        if resource.get("Type") == "AWS::ECS::TaskDefinition":
            props = resource.get("Properties", {})
            volumes = props.get("Volumes", [])
            if volumes:
                # Check for EFS or persistent volume configurations
                for vol in volumes:
                    efs_config = vol.get("EFSVolumeConfiguration")
                    if efs_config:
                        errors.append(f"{name} has EFS volume mount (violates Requirement 8.3)")

    return errors


def validate_log_group(template):
    """Validate CloudWatch Log Group exists."""
    resources = template.get("Resources", {})
    errors = []

    has_log_group = any(
        r.get("Type") == "AWS::Logs::LogGroup"
        for r in resources.values()
    )
    if not has_log_group:
        errors.append("Missing CloudWatch Logs LogGroup resource")

    return errors


def main():
    template_path = "cloudformation/template.yaml"
    print(f"Validating CloudFormation template: {template_path}")
    print("=" * 60)

    try:
        template = load_template(template_path)
    except yaml.YAMLError as e:
        print(f"FAIL: YAML parsing error: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"FAIL: Template file not found: {template_path}")
        sys.exit(1)

    all_errors = []

    # Run all validations
    checks = [
        ("Parameters (CpuAllocation, MemoryAllocation, EphemeralStorage)", validate_parameters),
        ("ECS Cluster with Fargate", validate_ecs_cluster),
        ("x86_64 Task Definition", lambda t: validate_task_definition(t, "TaskDefinitionX86", "X86_64")),
        ("arm64 Task Definition", lambda t: validate_task_definition(t, "TaskDefinitionArm64", "ARM64")),
        ("No persistent volumes (Req 8.3)", validate_no_persistent_volumes),
        ("CloudWatch Log Group", validate_log_group),
    ]

    for check_name, check_fn in checks:
        errors = check_fn(template)
        if errors:
            print(f"  FAIL: {check_name}")
            for err in errors:
                print(f"    - {err}")
            all_errors.extend(errors)
        else:
            print(f"  PASS: {check_name}")

    print("=" * 60)
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s) found")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED - Task 6.2 requirements verified")
        sys.exit(0)


if __name__ == "__main__":
    main()
