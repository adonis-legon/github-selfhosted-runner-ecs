# Requirements Document

## Introduction

This feature provides a self-hosted GitHub Actions runner infrastructure hosted on AWS ECS with Fargate tasks. The system enables running CI/CD pipelines defined in GitHub repositories by launching ephemeral runner containers in response to GitHub workflow events. The infrastructure is defined using CloudFormation and includes supporting scripts for image management and infrastructure lifecycle.

## Glossary

- **Runner_System**: The complete infrastructure and software system that hosts GitHub self-hosted runners on AWS ECS Fargate
- **ECS_Cluster**: The AWS Elastic Container Service cluster that orchestrates Fargate tasks running GitHub runner containers
- **Runner_Task**: A Fargate task that executes a single GitHub Actions workflow job as an ephemeral self-hosted runner
- **Runner_Image**: The Docker container image containing the GitHub Actions runner agent and supporting tools (including Docker-in-Docker capabilities)
- **VPC**: The Virtual Private Cloud network infrastructure containing public subnets where ECS tasks execute
- **Webhook_Handler**: The component that receives GitHub webhook events and triggers ECS task launches
- **PAT**: Personal Access Token used by the runner to authenticate with GitHub for repository cloning and runner registration
- **CloudFormation_Stack**: The AWS CloudFormation template(s) defining all infrastructure resources as code
- **Task_Definition**: The ECS task definition specifying container configuration, architecture, and resource allocation

## Requirements

### Requirement 1: ECS Cluster Infrastructure

**User Story:** As a DevOps engineer, I want an ECS cluster deployed via CloudFormation, so that I have a managed platform for running GitHub self-hosted runners.

#### Acceptance Criteria

1. THE CloudFormation_Stack SHALL define an ECS Cluster resource configured with the Fargate capacity provider for hosting Runner_Tasks
2. THE CloudFormation_Stack SHALL define a VPC with public subnets across at least two availability zones, including an Internet Gateway and route table entries directing outbound traffic (0.0.0.0/0) through the Internet Gateway
3. WHEN a Runner_Task is launched, THE ECS_Cluster SHALL place the task in a public subnet with a public IP address assigned using the Fargate launch type
4. THE CloudFormation_Stack SHALL define security groups that allow all outbound traffic to 0.0.0.0/0 and restrict inbound traffic to no ingress rules for Runner_Tasks
5. THE CloudFormation_Stack SHALL define a Task Execution IAM role granting permissions for ECS task execution and ECR image pull, and a Task IAM role with no additional permissions unless explicitly required by Runner_Task operations

### Requirement 2: Multi-Architecture Task Support

**User Story:** As a DevOps engineer, I want to run runners on both x86_64 and arm64 architectures, so that I can match the architecture requirements specified in GitHub Actions workflow files.

#### Acceptance Criteria

1. THE CloudFormation_Stack SHALL define a Task_Definition for x86_64 architecture with a configurable CPU allocation (valid Fargate values: 256, 512, 1024, 2048, 4096 CPU units) and a configurable memory allocation corresponding to the selected CPU tier
2. THE CloudFormation_Stack SHALL define a Task_Definition for arm64 architecture with a configurable CPU allocation (valid Fargate values: 256, 512, 1024, 2048, 4096 CPU units) and a configurable memory allocation corresponding to the selected CPU tier
3. WHEN a workflow job's `runs-on` labels include a label containing "x64" or "x86_64", THE Runner_System SHALL launch a Runner_Task using the x86_64 Task_Definition
4. WHEN a workflow job's `runs-on` labels include a label containing "arm64" or "arm", THE Runner_System SHALL launch a Runner_Task using the arm64 Task_Definition
5. THE Runner_Image SHALL be built and pushed for both linux/amd64 and linux/arm64 platforms
6. IF a workflow job's `runs-on` labels do not match any recognized architecture label, THEN THE Runner_System SHALL not launch a Runner_Task and SHALL log an error indicating the unrecognized label

### Requirement 3: GitHub Runner Registration and Authentication

**User Story:** As a DevOps engineer, I want runners to securely authenticate with GitHub, so that they can register as self-hosted runners and clone repository code.

#### Acceptance Criteria

1. THE Runner_Task SHALL use a PAT stored in AWS Secrets Manager or SSM Parameter Store to register with GitHub
2. WHEN a Runner_Task starts, THE Runner_Task SHALL obtain a registration token from the GitHub API using the stored PAT within 30 seconds of task startup
3. WHEN a Runner_Task starts, THE Runner_Task SHALL register itself as an ephemeral runner with the GitHub organization or repository specified via task environment configuration
4. WHEN a Runner_Task completes a job, THE Runner_Task SHALL deregister from GitHub and terminate the Fargate task
5. IF the PAT is invalid or expired, THEN THE Runner_Task SHALL log an authentication error and exit with a non-zero status code within 60 seconds of startup
6. IF the Runner_Task cannot retrieve the PAT from the secret store due to access denial or missing secret, THEN THE Runner_Task SHALL log an error indicating the secret retrieval failure and exit with a non-zero status code
7. IF the GitHub API is unreachable or returns a transient error during registration token retrieval, THEN THE Runner_Task SHALL retry up to 3 times with a 5-second delay between attempts before logging an error and exiting with a non-zero status code

### Requirement 4: GitHub Webhook Event Handling

**User Story:** As a DevOps engineer, I want the system to automatically launch runners when GitHub workflow jobs are queued, so that CI/CD pipelines execute without manual intervention.

#### Acceptance Criteria

1. THE Webhook_Handler SHALL expose an HTTPS endpoint to receive GitHub webhook events
2. WHEN a `workflow_job` event with action `queued` is received, THE Webhook_Handler SHALL launch a Runner_Task in the ECS_Cluster within 30 seconds of receiving the event
3. WHEN a webhook request is received, THE Webhook_Handler SHALL validate the webhook payload signature using the stored shared secret before processing the event
4. IF the webhook signature validation fails, THEN THE Webhook_Handler SHALL reject the request with HTTP 401 and log the attempt
5. WHEN launching a Runner_Task, THE Webhook_Handler SHALL match the runner labels in the webhook payload to the corresponding Task_Definition by architecture (x86_64 labels to the x86_64 Task_Definition, arm64 labels to the arm64 Task_Definition)
6. IF the runner labels in the webhook payload do not match any configured Task_Definition, THEN THE Webhook_Handler SHALL return HTTP 422 and log an error message indicating the unrecognized labels
7. IF the ECS_Cluster fails to launch the Runner_Task, THEN THE Webhook_Handler SHALL return HTTP 500 and log an error message indicating the launch failure reason
8. IF the webhook payload is missing required fields or is malformed, THEN THE Webhook_Handler SHALL reject the request with HTTP 400 and log an error message indicating the validation failure

### Requirement 5: Container Image Build Capability

**User Story:** As a developer, I want the runner to support building container images within GitHub Actions workflows, so that I can build and push Docker images as part of my CI/CD pipeline.

#### Acceptance Criteria

1. THE Runner_Image SHALL include Docker-in-Docker (DinD) or an equivalent container build tool that enables building container images inside the Runner_Task
2. WHEN a GitHub Actions workflow step invokes Docker build commands, THE Runner_Task SHALL execute the build within the task without requiring privileged host access beyond Fargate capabilities
3. THE Runner_Image SHALL include the following build tools: git, curl, jq, and a container runtime compatible with Fargate
4. IF a container build fails due to resource constraints, THEN THE Runner_Task SHALL report the failure back to GitHub Actions with an error message indicating the resource type exhausted (memory, CPU, or storage) and the requested versus available limits
5. WHEN a Runner_Task starts, THE container build tool SHALL be initialized and ready to accept build commands before the GitHub Actions workflow step executes
6. THE Task_Definition for Runner_Tasks that support container image builds SHALL allocate a minimum of 20 GB of ephemeral storage for image layer assembly and caching

### Requirement 6: Runner Image Build and Push Scripts

**User Story:** As a DevOps engineer, I want scripts to build and push the runner container image, so that I can maintain and update the runner image in ECR.

#### Acceptance Criteria

1. THE Runner_System SHALL provide a build script that builds the Runner_Image for both linux/amd64 and linux/arm64 platforms
2. THE Runner_System SHALL provide a push script that pushes the Runner_Image to an Amazon ECR repository
3. WHEN the build script is executed, THE build script SHALL tag the image with a version identifier and a `latest` tag
4. WHEN the push script is executed, THE push script SHALL authenticate with ECR before pushing
5. IF the ECR repository does not exist, THEN THE push script SHALL report an error indicating the repository must be created first

### Requirement 7: Infrastructure Lifecycle Scripts

**User Story:** As a DevOps engineer, I want scripts to create and destroy the AWS infrastructure, so that I can manage the full lifecycle of the runner environment.

#### Acceptance Criteria

1. THE Runner_System SHALL provide a create script that deploys the CloudFormation_Stack to AWS
2. THE Runner_System SHALL provide a destroy script that deletes the CloudFormation_Stack and associated resources from AWS
3. WHEN the create script is executed, THE create script SHALL validate the CloudFormation template using the AWS CloudFormation validate-template API call before initiating deployment
4. WHEN the destroy script is executed, THE destroy script SHALL prompt the user for confirmation by requiring the user to type the stack name, and SHALL abort deletion if the input does not match
5. IF the CloudFormation_Stack deployment fails, THEN THE create script SHALL display the stack events showing the failure reason and exit with a non-zero status code
6. THE create script SHALL accept parameters for stack name, AWS region, and PAT value
7. IF a required parameter (stack name, AWS region, or PAT value) is not provided to the create script, THEN THE create script SHALL display a usage message listing all required parameters and exit with a non-zero status code
8. WHEN the create script initiates deployment, THE create script SHALL wait for the CloudFormation_Stack to reach a terminal state (CREATE_COMPLETE or CREATE_FAILED) and display the final status before exiting
9. IF the destroy script is invoked with a stack name that does not exist, THEN THE destroy script SHALL display an error message indicating the stack was not found and exit with a non-zero status code

### Requirement 8: Ephemeral Runner Lifecycle

**User Story:** As a DevOps engineer, I want runners to be ephemeral and single-use, so that each job runs in a clean environment following GitHub security best practices.

#### Acceptance Criteria

1. THE Runner_Task SHALL configure the GitHub runner agent in ephemeral mode (single job execution) using the `--ephemeral` flag during registration
2. WHEN a Runner_Task completes its assigned job with any terminal status (success, failure, or cancellation), THE Runner_Task SHALL terminate and the Fargate task SHALL stop within 30 seconds of job completion
3. THE Runner_Task SHALL NOT use shared storage volumes (such as EFS or persistent EBS) and SHALL NOT persist filesystem state, environment variables, or cached data between workflow job executions
4. WHEN a Runner_Task is launched, THE Runner_Task SHALL start from the Runner_Image with no writable layers, artifacts, or environment state carried over from previous tasks
5. IF a Runner_Task does not receive a job assignment within 5 minutes of registration, THEN THE Runner_Task SHALL deregister from GitHub and terminate
6. IF a Runner_Task exceeds a maximum lifetime of 6 hours, THEN THE Runner_Task SHALL be forcibly stopped by the ECS_Cluster to prevent resource leaks from hung jobs
