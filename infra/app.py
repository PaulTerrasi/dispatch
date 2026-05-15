#!/usr/bin/env python3
"""CDK app: all Dispatch infrastructure in a single stack."""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as cf_origins
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct

# SSM parameter paths — values resolved at Lambda/Fargate cold start.
_SSM_CLAUDE_TOKEN = "/morning-digest/claude-oauth-token"
_SSM_AUTH_TOKEN = "/morning-digest/auth-token"
# NYT_COOKIES is populated out-of-band by scripts/refresh_nyt_cookies.py
# running on a personal machine where the user is logged into NYT. See the
# "NYT subscription cookies" section of the README for install instructions.
# Treated as optional — runtime tolerates a missing parameter.
_SSM_NYT_COOKIES = "/morning-digest/nyt-cookies"

# Where alarm notifications go.
_ALERT_EMAIL = "paul.a.terrasi@gmail.com"

REPO_ROOT = str(Path(__file__).parent.parent)

# DockerImageAsset honors .dockerignore — keep that as the single source of truth.

# Default VPC for Fargate tasks. Can be overridden via env vars at synth time
# without touching code (useful when redeploying to a new account/region whose
# default VPC has different IDs).
_DEFAULT_VPC_ID = os.environ.get("MD_VPC_ID", "vpc-0fa048d14c07175b5")
_DEFAULT_VPC_SUBNETS = os.environ.get(
    "MD_SUBNET_IDS",
    "subnet-03274f7bfe2e9c348,"
    "subnet-07a5ca819e7b5bb23,"
    "subnet-0982ded26de577ebe,"
    "subnet-05115be1c79102a91,"
    "subnet-05891bde950e6bb12,"
    "subnet-0158539461c48b21d",
).split(",")


class MorningDigestStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs: object) -> None:
        super().__init__(scope, id, **kwargs)

        # ── S3 buckets ────────────────────────────────────────────────────────
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"morning-digest-data-{self.account}",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="archive-old-versions",
                    noncurrent_version_transitions=[
                        s3.NoncurrentVersionTransition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=cdk.Duration.days(90),
                        )
                    ],
                )
            ],
        )

        frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"morning-digest-frontend-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── IAM role (shared by Lambda and ECS task) ──────────────────────────
        app_role = iam.Role(
            self,
            "AppRole",
            role_name="morning-digest-app",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("lambda.amazonaws.com"),
                iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )
        data_bucket.grant_read_write(app_role)
        app_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/morning-digest/*"],
            )
        )
        # Allow ECS task to pull images from ECR
        app_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly")
        )
        app_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=["*"],
            )
        )

        # ── Env vars (SSM paths resolved at Lambda/Fargate cold start) ────────
        common_env = {
            "MORNING_DIGEST_S3_BUCKET": data_bucket.bucket_name,
            "CLAUDE_CODE_OAUTH_TOKEN": _SSM_CLAUDE_TOKEN,
            "MORNING_DIGEST_AUTH_TOKEN": _SSM_AUTH_TOKEN,
            "NYT_COOKIES": _SSM_NYT_COOKIES,
        }

        # ── Lambda: FastAPI via AWS Lambda Web Adapter (uvicorn on :8080) ──
        lambda_image = lambda_.DockerImageCode.from_image_asset(
            REPO_ROOT,
            file="Dockerfile.lambda",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )
        # api_env extends common_env with ECS coords (set after task def is created below)
        api_env = dict(common_env)
        # Lambda's filesystem is read-only outside /tmp, and its default HOME
        # (/home/sbx_userNNNN) doesn't exist. The bundled `claude` CLI writes
        # config on first run, so point it at /tmp or it hangs the initialize
        # handshake until the SDK's 60s timeout fires.
        api_env["HOME"] = "/tmp"
        api_env["CLAUDE_CONFIG_DIR"] = "/tmp/.claude"
        # Make the Node heap ceiling explicit for the bundled Claude CLI
        # subprocess. Node's cgroup-based default detection isn't guaranteed
        # to follow the Lambda memory bump, and we've seen silent exit-1
        # crashes consistent with heap pressure. 1536MB leaves ~512MB for
        # the uvicorn Python process inside the same 2048MB Lambda.
        api_env["NODE_OPTIONS"] = "--max-old-space-size=1536"
        api_fn = lambda_.DockerImageFunction(
            self,
            "ApiFunction",
            function_name="morning-digest-api",
            code=lambda_image,
            role=app_role,
            # 2048MB: the bundled Claude CLI subprocess (Node.js) was exiting
            # silently with code 1 mid-chat at 1024MB, with no stderr lines
            # captured — consistent with Node heap pressure inside the
            # subprocess even though the Lambda itself was well under its limit.
            memory_size=2048,
            # 5-minute cap so a long chat-tab agent turn (with tool calls that
            # may web_fetch) cannot hit the Lambda max. Most invocations finish
            # in seconds.
            timeout=cdk.Duration.minutes(5),
            environment=api_env,
        )

        # ── Lambda Function URL with response streaming ──────────────────────
        # We invoke the Lambda directly via its Function URL (no API Gateway).
        # InvokeMode.RESPONSE_STREAM is what makes SSE work end-to-end —
        # API Gateway HTTP API and Mangum both buffered the full response,
        # which broke streaming. AWS Lambda Web Adapter (see Dockerfile.lambda)
        # bridges the Lambda Runtime API to the uvicorn HTTP server and
        # forwards streamed chunks unchanged.
        #
        # Auth is enforced inside the app by BearerAuthMiddleware against
        # MORNING_DIGEST_AUTH_TOKEN, so the Function URL itself is open
        # (NONE). The URL host is unguessable but not secret.
        api_fn_url = api_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
        )

        # ── ECS Fargate: daily runner ─────────────────────────────────────────
        # Reference existing default VPC by ID to avoid CDK creating a new one with NAT gateways.
        default_vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "DefaultVpc",
            vpc_id=_DEFAULT_VPC_ID,
            availability_zones=[
                "us-east-1a",
                "us-east-1b",
                "us-east-1c",
                "us-east-1d",
                "us-east-1e",
                "us-east-1f",
            ],
            public_subnet_ids=_DEFAULT_VPC_SUBNETS,
        )
        cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name="morning-digest",
            vpc=default_vpc,
            enable_fargate_capacity_providers=True,
        )

        runner_image = ecr_assets.DockerImageAsset(
            self,
            "RunnerImage",
            directory=REPO_ROOT,
            file="Dockerfile.fargate",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        task_def = ecs.FargateTaskDefinition(
            self,
            "RunnerTaskDef",
            family="morning-digest-runner",
            cpu=512,
            memory_limit_mib=1024,
            task_role=app_role,
            execution_role=app_role,
        )
        # Explicit log group so we can attach a metric filter for failure alarms.
        runner_log_group = logs.LogGroup(
            self,
            "RunnerLogGroup",
            log_group_name="/ecs/morning-digest-runner",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        task_def.add_container(
            "Runner",
            image=ecs.ContainerImage.from_docker_image_asset(runner_image),
            environment=common_env,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="morning-digest-runner",
                log_group=runner_log_group,
            ),
        )

        # ── Wire API Lambda so it can spawn reflection tasks on feedback ──────
        api_fn.add_environment("MORNING_DIGEST_REFLECT_CLUSTER", cluster.cluster_name)
        api_fn.add_environment("MORNING_DIGEST_REFLECT_TASK_DEF", task_def.task_definition_arn)
        api_fn.add_environment("MORNING_DIGEST_REFLECT_SUBNETS", ",".join(_DEFAULT_VPC_SUBNETS))
        app_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_def.task_definition_arn],
            )
        )
        app_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[app_role.role_arn],
            )
        )

        # EventBridge Scheduler: hourly (top of every hour, America/New_York)
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_def.task_definition_arn],
            )
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[app_role.role_arn],
            )
        )

        scheduler.CfnSchedule(
            self,
            "DailyRunSchedule",
            name="morning-digest-hourly",
            schedule_expression="cron(0 * * * ? *)",
            schedule_expression_timezone="America/New_York",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF",
            ),
            target=scheduler.CfnSchedule.TargetProperty(
                arn="arn:aws:scheduler:::aws-sdk:ecs:runTask",
                role_arn=scheduler_role.role_arn,
                # AWS SDK target input must use PascalCase parameter names.
                # Hourly run is curation-only; reflection is event-driven via the
                # feedback API (--reflect-drain).
                input=cdk.Stack.of(self).to_json_string(
                    {
                        "Cluster": cluster.cluster_name,
                        "TaskDefinition": task_def.task_definition_arn,
                        "NetworkConfiguration": {
                            "AwsvpcConfiguration": {
                                "Subnets": _DEFAULT_VPC_SUBNETS,
                                "AssignPublicIp": "ENABLED",
                            }
                        },
                        "CapacityProviderStrategy": [
                            {"CapacityProvider": "FARGATE_SPOT", "Weight": 1},
                            {"CapacityProvider": "FARGATE", "Weight": 0, "Base": 0},
                        ],
                    }
                ),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=2,
                    maximum_event_age_in_seconds=600,
                ),
            ),
        )

        # ── CloudFront: PWA + API behind one domain ───────────────────────────
        oac = cf.S3OriginAccessControl(
            self,
            "FrontendOAC",
            signing=cf.Signing.SIGV4_NO_OVERRIDE,
        )

        frontend_origin = cf_origins.S3BucketOrigin.with_origin_access_control(
            frontend_bucket,
            origin_access_control=oac,
        )

        # Function URLs look like https://<id>.lambda-url.<region>.on.aws/.
        # Strip the protocol and trailing slash so cf_origins.HttpOrigin gets a
        # bare hostname. cdk.Fn.select + Fn.split is needed because the URL is
        # only known at deploy time (it's a CloudFormation token).
        api_origin_domain = cdk.Fn.select(2, cdk.Fn.split("/", api_fn_url.url))
        api_origin = cf_origins.HttpOrigin(
            api_origin_domain,
            # Heartbeats keep the SSE stream live across CloudFront's read
            # timeout. 60s is the max without a service quota request.
            read_timeout=cdk.Duration.seconds(60),
        )

        distribution = cf.Distribution(
            self,
            "Distribution",
            comment="morning-digest",
            default_behavior=cf.BehaviorOptions(
                origin=frontend_origin,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=cf.OriginRequestPolicy.CORS_S3_ORIGIN,
            ),
            additional_behaviors={
                "/api/*": cf.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cf.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                ),
            },
            error_responses=[
                cf.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
                cf.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
            ],
        )

        # Grant CloudFront OAC read access to frontend bucket
        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[frontend_bucket.arn_for_objects("*")],
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": (
                            f"arn:aws:cloudfront::{self.account}:distribution/"
                            f"{distribution.distribution_id}"
                        )
                    }
                },
            )
        )

        # ── Alerts: SNS topic + CloudWatch metric filter + alarm on run failures ─
        alerts_topic = sns.Topic(
            self,
            "AlertsTopic",
            topic_name="morning-digest-alerts",
            display_name="Morning Digest alerts",
        )
        alerts_topic.add_subscription(sns_subs.EmailSubscription(_ALERT_EMAIL))

        # Metric filter scans the runner log group for structlog JSON events that
        # indicate a failed run. Every match increments MorningDigest/RunFailures
        # by 1; the alarm fires if ≥ 1 match in any 1-hour window.
        run_failure_metric = logs.MetricFilter(
            self,
            "RunFailureMetricFilter",
            log_group=runner_log_group,
            metric_namespace="MorningDigest",
            metric_name="RunFailures",
            metric_value="1",
            default_value=0,
            filter_pattern=logs.FilterPattern.literal(
                '{ ($.event = "run.curation_failed") '
                '|| ($.event = "run.done" && $.exit_reason = "error") }'
            ),
        )

        run_failure_alarm = cw.Alarm(
            self,
            "RunFailureAlarm",
            alarm_name="morning-digest-run-failed",
            alarm_description=(
                "Fires when the curation runner logs a failure event. "
                "Investigate the runner log group: /ecs/morning-digest-runner."
            ),
            metric=run_failure_metric.metric(statistic="Sum", period=cdk.Duration.hours(1)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        run_failure_alarm.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # ── Outputs ──────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self, "CloudFrontUrl", value=f"https://{distribution.distribution_domain_name}"
        )
        cdk.CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        cdk.CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        cdk.CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        cdk.CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        cdk.CfnOutput(self, "TaskDefArn", value=task_def.task_definition_arn)
        cdk.CfnOutput(
            self,
            "FrontendDeployCmd",
            value=(
                f"cd pwa && npm run build && "
                f"aws s3 sync dist/ s3://{frontend_bucket.bucket_name}/ --delete && "
                f"aws cloudfront create-invalidation "
                f"--distribution-id {distribution.distribution_id} --paths '/*'"
            ),
        )


app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)
MorningDigestStack(app, "MorningDigest", env=env)
app.synth()
