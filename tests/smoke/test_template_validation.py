"""
Smoke tests for CloudFormation template structural validation.

These tests load and parse the CloudFormation template YAML and validate
that required resources and configurations are present.

Validates: Requirements 1.1, 1.2, 1.4, 2.1, 2.2, 5.6, 3.1, 8.1
"""

import os
import yaml
import pytest

# Paths relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "cloudformation", "template.yaml")
ENTRYPOINT_PATH = os.path.join(PROJECT_ROOT, "docker", "entrypoint.sh")


# Custom YAML loader that handles CloudFormation intrinsic function tags
# (!Sub, !Ref, !GetAtt, !Join, !Select, !Split, !If, !Equals, etc.)
class CfnLoader(yaml.SafeLoader):
    pass


def _cfn_tag_constructor(loader, tag_suffix, node):
    """Handle any CloudFormation intrinsic function tag by returning its value as-is."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)


# Register a multi-constructor that catches all '!' tags
CfnLoader.add_multi_constructor("!", _cfn_tag_constructor)


@pytest.fixture
def template():
    """Load and parse the CloudFormation template."""
    with open(TEMPLATE_PATH, "r") as f:
        return yaml.load(f, Loader=CfnLoader)


@pytest.fixture
def entrypoint_script():
    """Load the entrypoint script content."""
    with open(ENTRYPOINT_PATH, "r") as f:
        return f.read()


def get_resources_by_type(template, resource_type):
    """Return all resources matching the given CloudFormation type."""
    resources = template.get("Resources", {})
    return {
        name: props
        for name, props in resources.items()
        if props.get("Type") == resource_type
    }


class TestECSClusterWithFargate:
    """Validate template contains ECS Cluster with Fargate provider (Req 1.1)."""

    def test_ecs_cluster_exists(self, template):
        clusters = get_resources_by_type(template, "AWS::ECS::Cluster")
        assert len(clusters) >= 1, "Template must contain at least one ECS Cluster"

    def test_ecs_cluster_has_fargate_capacity_provider(self, template):
        clusters = get_resources_by_type(template, "AWS::ECS::Cluster")
        for name, cluster in clusters.items():
            props = cluster.get("Properties", {})
            capacity_providers = props.get("CapacityProviders", [])
            assert "FARGATE" in capacity_providers, (
                f"ECS Cluster '{name}' must include FARGATE capacity provider"
            )


class TestVPCWithPublicSubnets:
    """Validate template contains VPC with 2+ public subnets (Req 1.2)."""

    def test_vpc_exists(self, template):
        vpcs = get_resources_by_type(template, "AWS::EC2::VPC")
        assert len(vpcs) >= 1, "Template must contain at least one VPC"

    def test_at_least_two_public_subnets(self, template):
        subnets = get_resources_by_type(template, "AWS::EC2::Subnet")
        public_subnets = [
            name
            for name, subnet in subnets.items()
            if subnet.get("Properties", {}).get("MapPublicIpOnLaunch") is True
        ]
        assert len(public_subnets) >= 2, (
            f"Template must contain at least 2 public subnets, found {len(public_subnets)}"
        )

    def test_internet_gateway_exists(self, template):
        igws = get_resources_by_type(template, "AWS::EC2::InternetGateway")
        assert len(igws) >= 1, "Template must contain an Internet Gateway"


class TestSecurityGroupEgressOnly:
    """Validate template contains security group with egress-only rules (Req 1.4)."""

    def test_security_group_exists(self, template):
        sgs = get_resources_by_type(template, "AWS::EC2::SecurityGroup")
        assert len(sgs) >= 1, "Template must contain at least one Security Group"

    def test_security_group_has_egress_rules(self, template):
        sgs = get_resources_by_type(template, "AWS::EC2::SecurityGroup")
        for name, sg in sgs.items():
            props = sg.get("Properties", {})
            egress = props.get("SecurityGroupEgress", [])
            assert len(egress) >= 1, (
                f"Security Group '{name}' must have at least one egress rule"
            )

    def test_security_group_has_no_ingress_rules(self, template):
        sgs = get_resources_by_type(template, "AWS::EC2::SecurityGroup")
        for name, sg in sgs.items():
            props = sg.get("Properties", {})
            ingress = props.get("SecurityGroupIngress", [])
            assert len(ingress) == 0, (
                f"Security Group '{name}' must have no ingress rules (egress-only)"
            )


class TestTaskDefinitions:
    """Validate template contains x86_64 and arm64 Task Definitions (Req 2.1, 2.2)."""

    def test_x86_64_task_definition_exists(self, template):
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        x86_defs = [
            name
            for name, td in task_defs.items()
            if td.get("Properties", {})
            .get("RuntimePlatform", {})
            .get("CpuArchitecture")
            == "X86_64"
        ]
        assert len(x86_defs) >= 1, (
            "Template must contain at least one x86_64 Task Definition"
        )

    def test_arm64_task_definition_exists(self, template):
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        arm_defs = [
            name
            for name, td in task_defs.items()
            if td.get("Properties", {})
            .get("RuntimePlatform", {})
            .get("CpuArchitecture")
            == "ARM64"
        ]
        assert len(arm_defs) >= 1, (
            "Template must contain at least one arm64 Task Definition"
        )

    def test_task_definitions_use_fargate(self, template):
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        for name, td in task_defs.items():
            compat = td.get("Properties", {}).get("RequiresCompatibilities", [])
            assert "FARGATE" in compat, (
                f"Task Definition '{name}' must require FARGATE compatibility"
            )


class TestEphemeralStorage:
    """Validate template contains ephemeral storage >= 20 GiB (Req 5.6)."""

    def test_task_definitions_have_sufficient_ephemeral_storage(self, template):
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        assert len(task_defs) >= 1, "Template must contain at least one Task Definition"

        for name, td in task_defs.items():
            props = td.get("Properties", {})
            ephemeral = props.get("EphemeralStorage", {})
            size = ephemeral.get("SizeInGiB")
            # The template may use a !Ref which our CfnLoader resolves to a plain string
            if isinstance(size, str):
                # It's a parameter reference; validate via the parameter's Default/MinValue
                param_name = size
                params = template.get("Parameters", {})
                param = params.get(param_name, {})
                min_value = param.get("MinValue")
                default_value = param.get("Default")
                assert default_value is not None and default_value >= 20, (
                    f"Parameter '{param_name}' Default must be >= 20 GiB, got {default_value}"
                )
                if min_value is not None:
                    assert min_value >= 20, (
                        f"Parameter '{param_name}' MinValue must be >= 20 GiB, got {min_value}"
                    )
            elif isinstance(size, dict) and "Ref" in size:
                # Fallback for loaders that produce {"Ref": "..."} dicts
                param_name = size["Ref"]
                params = template.get("Parameters", {})
                param = params.get(param_name, {})
                min_value = param.get("MinValue")
                default_value = param.get("Default")
                assert default_value is not None and default_value >= 20, (
                    f"Parameter '{param_name}' Default must be >= 20 GiB, got {default_value}"
                )
                if min_value is not None:
                    assert min_value >= 20, (
                        f"Parameter '{param_name}' MinValue must be >= 20 GiB, got {min_value}"
                    )
            elif isinstance(size, (int, float)):
                assert size >= 20, (
                    f"Task Definition '{name}' ephemeral storage must be >= 20 GiB, got {size}"
                )
            else:
                pytest.fail(
                    f"Task Definition '{name}' must specify EphemeralStorage.SizeInGiB"
                )


class TestSSMParameterForPAT:
    """Validate template contains SSM parameter for PAT (Req 3.1)."""

    def test_ssm_parameter_for_pat_exists(self, template):
        ssm_params = get_resources_by_type(template, "AWS::SSM::Parameter")
        pat_params = [
            name
            for name, param in ssm_params.items()
            if "pat" in param.get("Properties", {}).get("Name", "").lower()
        ]
        assert len(pat_params) >= 1, (
            "Template must contain an SSM Parameter for the GitHub PAT"
        )


class TestEntrypointEphemeralFlag:
    """Validate entrypoint script includes --ephemeral flag (Req 8.1)."""

    def test_entrypoint_contains_ephemeral_flag(self, entrypoint_script):
        assert "--ephemeral" in entrypoint_script, (
            "Entrypoint script must include the --ephemeral flag for single-use runner mode"
        )


class TestTaskDefinitionEntrypointWiring:
    """Validate task definition environment variables match entrypoint expectations (Req 3.1, 3.2, 3.3, 8.1, 8.3, 8.4)."""

    REQUIRED_ENV_VARS = ["GITHUB_ORG", "RUNNER_LABELS", "PAT_SSM_PARAM", "AWS_REGION"]

    def _get_container_env_names(self, task_def):
        """Extract environment variable names from a task definition's container definitions."""
        props = task_def.get("Properties", {})
        containers = props.get("ContainerDefinitions", [])
        env_names = []
        for container in containers:
            for env in container.get("Environment", []):
                env_names.append(env.get("Name"))
        return env_names

    def _get_container_secrets(self, task_def):
        """Extract secrets from a task definition's container definitions."""
        props = task_def.get("Properties", {})
        containers = props.get("ContainerDefinitions", [])
        secrets = []
        for container in containers:
            for secret in container.get("Secrets", []):
                secrets.append(secret)
        return secrets

    def test_x86_task_def_has_required_env_vars(self, template):
        """Verify x86_64 task definition passes all env vars expected by entrypoint."""
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        x86_defs = {
            name: td
            for name, td in task_defs.items()
            if td.get("Properties", {})
            .get("RuntimePlatform", {})
            .get("CpuArchitecture")
            == "X86_64"
        }
        assert len(x86_defs) >= 1, "Must have at least one x86_64 task definition"

        for name, td in x86_defs.items():
            env_names = self._get_container_env_names(td)
            for var in self.REQUIRED_ENV_VARS:
                assert var in env_names, (
                    f"x86_64 Task Definition '{name}' must include environment variable '{var}'"
                )

    def test_arm64_task_def_has_required_env_vars(self, template):
        """Verify arm64 task definition passes all env vars expected by entrypoint."""
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        arm_defs = {
            name: td
            for name, td in task_defs.items()
            if td.get("Properties", {})
            .get("RuntimePlatform", {})
            .get("CpuArchitecture")
            == "ARM64"
        }
        assert len(arm_defs) >= 1, "Must have at least one arm64 task definition"

        for name, td in arm_defs.items():
            env_names = self._get_container_env_names(td)
            for var in self.REQUIRED_ENV_VARS:
                assert var in env_names, (
                    f"arm64 Task Definition '{name}' must include environment variable '{var}'"
                )

    def test_task_definitions_have_pat_secret(self, template):
        """Verify secrets configuration passes PAT from SSM to container (Req 3.1)."""
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        for name, td in task_defs.items():
            secrets = self._get_container_secrets(td)
            pat_secrets = [s for s in secrets if s.get("Name") == "GITHUB_PAT"]
            assert len(pat_secrets) >= 1, (
                f"Task Definition '{name}' must have a GITHUB_PAT secret from SSM"
            )
            # Verify the ValueFrom references the PAT SSM parameter
            value_from = pat_secrets[0].get("ValueFrom", "")
            assert "github-runner/pat" in str(value_from), (
                f"Task Definition '{name}' GITHUB_PAT secret must reference /github-runner/pat SSM parameter"
            )

    def test_task_definitions_have_no_persistent_volumes(self, template):
        """Verify no persistent volume mounts in task definitions (Req 8.3, 8.4)."""
        task_defs = get_resources_by_type(template, "AWS::ECS::TaskDefinition")
        for name, td in task_defs.items():
            props = td.get("Properties", {})
            volumes = props.get("Volumes", [])
            # Filter out any EFS or persistent volumes
            persistent_volumes = [
                v
                for v in volumes
                if v.get("EFSVolumeConfiguration") is not None
                or v.get("Host", {}).get("SourcePath") is not None
            ]
            assert len(persistent_volumes) == 0, (
                f"Task Definition '{name}' must not have persistent volume mounts (EFS/EBS). "
                f"Runners must be ephemeral with no shared storage (Req 8.3, 8.4)."
            )

    def test_entrypoint_expects_same_env_vars_as_task_def(self, entrypoint_script, template):
        """Cross-check that entrypoint references the same env vars provided by task definitions."""
        # Verify entrypoint uses the env vars that task definitions provide
        for var in self.REQUIRED_ENV_VARS:
            assert var in entrypoint_script, (
                f"Entrypoint script must reference environment variable '{var}'"
            )
