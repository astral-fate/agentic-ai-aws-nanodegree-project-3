<#
.SYNOPSIS
    Inventory and tear down the AWS resources created by the Udacity nanodegree
    projects, across regions, without touching anything else in the account.

.DESCRIPTION
    Upload to AWS CloudShell and run with pwsh. It uses CloudShell's own
    credentials, so nothing needs configuring.

        pwsh ./teardown-all.ps1                 # INVENTORY ONLY - deletes nothing
        pwsh ./teardown-all.ps1 -Delete         # delete what the inventory listed
        pwsh ./teardown-all.ps1 -Regions us-east-1,eu-north-1
        pwsh ./teardown-all.ps1 -Delete -IncludeS3   # opt in to S3 (off by default)

    WHY IT IS BUILT THIS WAY

    This account is named "Saudi Space Tech", so "delete everything not related
    to Saudi Space" cannot be a filter - it would match the whole account. A
    script that deletes whatever does not appear on a keep-list is one typo away
    from destroying production, and AWS deletions are not reversible.

    So this does the opposite. It only deletes resources whose NAME matches one
    of the project prefixes in $TargetPatterns below. Everything else in the
    account is listed as "kept" so you can see it was seen and left alone. If
    something you want gone is not matched, add a pattern - do not invert the
    logic.

    S3 BUCKETS ARE NEVER DELETED unless you pass -IncludeS3, and even then only
    buckets whose name matches a target pattern. S3 is where the account's real
    data lives.

.NOTES
    Costs, in rough order of what actually bills while idle:
      - Bedrock Knowledge Bases + S3 Vectors indexes  (the expensive idle ones)
      - OpenSearch Serverless collections             (~$0.24/OCU-hr, min 2 OCU)
      - AgentCore runtimes / memories / gateways
      - API Gateway, Lambda, DynamoDB, CloudWatch logs (pennies, or free)
#>

[CmdletBinding()]
param(
    [switch] $Delete,
    [switch] $IncludeS3,
    [switch] $Force,
    [string[]] $Regions = @('us-east-1', 'us-west-2', 'eu-west-1', 'eu-north-1'),
    [string[]] $ExtraPatterns = @()
)

$ErrorActionPreference = 'Continue'

# ── What counts as "ours" ────────────────────────────────────────────────────
# Every deletion below is gated on a name matching one of these. Anything that
# does not match is reported as kept and never touched.
$TargetPatterns = @(
    'udacity-agentcore',   # project 3 stack, tables, buckets, role, guardrail
    'udacity_agentcore',   # underscore variants (runtime, memory)
    'novamart',            # project 3 knowledge bases, venv, state
    'cs-agent',            # project 2 prefix
    'customer-support',    # project 2 gateway / memory
    'CustomerSupport',     # project 2 CamelCase resources
    'order-tracker',       # project 2 lambda
    'refund-processor',    # project 2 lambda
    'support-chatbot'      # project 1/2 guardrail, seen in the console
) + $ExtraPatterns

function Test-IsTarget([string] $Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    foreach ($p in $TargetPatterns) {
        if ($Name -like "*$p*") { return $true }
    }
    return $false
}

# ── Output helpers ───────────────────────────────────────────────────────────
function Write-Head([string] $Text) { Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Hit ([string] $Text) { Write-Host "   [TARGET] $Text" -ForegroundColor Yellow }
function Write-Keep([string] $Text) { Write-Host "   [keep]   $Text" -ForegroundColor DarkGray }
function Write-Done([string] $Text) { Write-Host "   [deleted] $Text" -ForegroundColor Green }
function Write-Fail([string] $Text) { Write-Host "   [FAILED] $Text" -ForegroundColor Red }

$Found   = [System.Collections.ArrayList]::new()
$Results = [System.Collections.ArrayList]::new()

function Add-Found($Region, $Kind, $Name, $Id, $DeleteBlock) {
    [void]$Found.Add([pscustomobject]@{
        Region = $Region; Kind = $Kind; Name = $Name; Id = $Id; Delete = $DeleteBlock
    })
}

# Run an AWS CLI call and return parsed JSON, or $null. AWS CLI writes some
# benign notices to stderr, so stderr is discarded rather than treated as error.
function Invoke-Aws([string[]] $CliArgs) {
    try {
        $raw = & aws @CliArgs 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        return ($raw | ConvertFrom-Json)
    } catch { return $null }
}

Write-Host ""
Write-Host "AWS nanodegree teardown" -ForegroundColor White
$ident = Invoke-Aws @('sts','get-caller-identity','--output','json')
if (-not $ident) {
    Write-Fail "No AWS credentials. Run this inside AWS CloudShell."
    exit 1
}
Write-Host "  account : $($ident.Account)"
Write-Host "  identity: $($ident.Arn)"
Write-Host "  regions : $($Regions -join ', ')"
Write-Host "  mode    : $(if ($Delete) { 'DELETE' } else { 'INVENTORY ONLY (nothing will be deleted)' })" `
    -ForegroundColor $(if ($Delete) { 'Red' } else { 'Green' })
Write-Host "  S3      : $(if ($IncludeS3) { 'included (matching names only)' } else { 'PROTECTED - never deleted' })"

# ─────────────────────────────────────────────────────────────────────────────
#  DISCOVERY - per region, most-expensive-while-idle first
# ─────────────────────────────────────────────────────────────────────────────
foreach ($r in $Regions) {
    Write-Head "Region $r"

    # 1. Bedrock Knowledge Bases - bill for storage while idle
    $kbs = Invoke-Aws @('bedrock-agent','list-knowledge-bases','--region',$r,'--output','json')
    foreach ($kb in $kbs.knowledgeBaseSummaries) {
        if (Test-IsTarget $kb.name) {
            Write-Hit "KnowledgeBase $($kb.name) ($($kb.knowledgeBaseId))"
            Add-Found $r 'knowledge-base' $kb.name $kb.knowledgeBaseId {
                param($x) Invoke-Aws @('bedrock-agent','delete-knowledge-base',
                    '--knowledge-base-id',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "KnowledgeBase $($kb.name)" }
    }

    # 2. S3 Vectors - the index behind each KB, also bills while idle
    $vb = Invoke-Aws @('s3vectors','list-vector-buckets','--region',$r,'--output','json')
    foreach ($b in $vb.vectorBuckets) {
        if (Test-IsTarget $b.vectorBucketName) {
            $ix = Invoke-Aws @('s3vectors','list-indexes','--vector-bucket-name',$b.vectorBucketName,
                '--region',$r,'--output','json')
            foreach ($i in $ix.indexes) {
                Write-Hit "S3VectorIndex $($b.vectorBucketName)/$($i.indexName)"
                Add-Found $r 's3-vector-index' "$($b.vectorBucketName)/$($i.indexName)" $b.vectorBucketName {
                    param($x)
                    $parts = $x.Name -split '/'
                    Invoke-Aws @('s3vectors','delete-index','--vector-bucket-name',$parts[0],
                        '--index-name',$parts[1],'--region',$x.Region,'--output','json') | Out-Null
                }
            }
            Write-Hit "S3VectorBucket $($b.vectorBucketName)"
            Add-Found $r 's3-vector-bucket' $b.vectorBucketName $b.vectorBucketName {
                param($x) Invoke-Aws @('s3vectors','delete-vector-bucket',
                    '--vector-bucket-name',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "S3VectorBucket $($b.vectorBucketName)" }
    }

    # 3. OpenSearch Serverless - project 2; ~$0.24/OCU-hr, 2 OCU minimum
    $cols = Invoke-Aws @('opensearchserverless','list-collections','--region',$r,'--output','json')
    foreach ($c in $cols.collectionSummaries) {
        if (Test-IsTarget $c.name) {
            Write-Hit "OpenSearchCollection $($c.name)  <-- bills hourly"
            Add-Found $r 'opensearch-collection' $c.name $c.id {
                param($x) Invoke-Aws @('opensearchserverless','delete-collection',
                    '--id',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "OpenSearchCollection $($c.name)" }
    }

    # 4. AgentCore runtimes / memories / gateways
    $rts = Invoke-Aws @('bedrock-agentcore-control','list-agent-runtimes','--region',$r,'--output','json')
    foreach ($rt in $rts.agentRuntimes) {
        if (Test-IsTarget $rt.agentRuntimeName) {
            Write-Hit "AgentCoreRuntime $($rt.agentRuntimeName)"
            Add-Found $r 'agentcore-runtime' $rt.agentRuntimeName $rt.agentRuntimeId {
                param($x) Invoke-Aws @('bedrock-agentcore-control','delete-agent-runtime',
                    '--agent-runtime-id',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "AgentCoreRuntime $($rt.agentRuntimeName)" }
    }

    $mems = Invoke-Aws @('bedrock-agentcore-control','list-memories','--region',$r,'--output','json')
    foreach ($m in $mems.memories) {
        $mname = if ($m.name) { $m.name } else { $m.id }
        if (Test-IsTarget $mname) {
            Write-Hit "AgentCoreMemory $mname"
            Add-Found $r 'agentcore-memory' $mname $m.id {
                param($x) Invoke-Aws @('bedrock-agentcore-control','delete-memory',
                    '--memory-id',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "AgentCoreMemory $mname" }
    }

    $gws = Invoke-Aws @('bedrock-agentcore-control','list-gateways','--region',$r,'--output','json')
    foreach ($g in $gws.items) {
        $gname = if ($g.name) { $g.name } else { $g.gatewayId }
        if (Test-IsTarget $gname) {
            Write-Hit "AgentCoreGateway $gname"
            Add-Found $r 'agentcore-gateway' $gname $g.gatewayId {
                param($x) Invoke-Aws @('bedrock-agentcore-control','delete-gateway',
                    '--gateway-identifier',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "AgentCoreGateway $gname" }
    }

    # 5. Bedrock Guardrails
    $grs = Invoke-Aws @('bedrock','list-guardrails','--region',$r,'--output','json')
    foreach ($g in $grs.guardrails) {
        if (Test-IsTarget $g.name) {
            Write-Hit "Guardrail $($g.name)"
            Add-Found $r 'guardrail' $g.name $g.id {
                param($x) Invoke-Aws @('bedrock','delete-guardrail',
                    '--guardrail-identifier',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "Guardrail $($g.name)" }
    }

    # 6. Lambda
    $fns = Invoke-Aws @('lambda','list-functions','--region',$r,'--output','json')
    foreach ($f in $fns.Functions) {
        if (Test-IsTarget $f.FunctionName) {
            Write-Hit "Lambda $($f.FunctionName)"
            Add-Found $r 'lambda' $f.FunctionName $f.FunctionName {
                param($x) Invoke-Aws @('lambda','delete-function',
                    '--function-name',$x.Id,'--region',$x.Region) | Out-Null
            }
        } else { Write-Keep "Lambda $($f.FunctionName)" }
    }

    # 7. API Gateway (REST)
    $apis = Invoke-Aws @('apigateway','get-rest-apis','--region',$r,'--output','json')
    foreach ($a in $apis.items) {
        if (Test-IsTarget $a.name) {
            Write-Hit "ApiGateway $($a.name)"
            Add-Found $r 'apigateway' $a.name $a.id {
                param($x) Invoke-Aws @('apigateway','delete-rest-api',
                    '--rest-api-id',$x.Id,'--region',$x.Region) | Out-Null
            }
        } else { Write-Keep "ApiGateway $($a.name)" }
    }

    # 8. DynamoDB
    $tabs = Invoke-Aws @('dynamodb','list-tables','--region',$r,'--output','json')
    foreach ($t in $tabs.TableNames) {
        if (Test-IsTarget $t) {
            Write-Hit "DynamoDB $t"
            Add-Found $r 'dynamodb' $t $t {
                param($x) Invoke-Aws @('dynamodb','delete-table',
                    '--table-name',$x.Id,'--region',$x.Region,'--output','json') | Out-Null
            }
        } else { Write-Keep "DynamoDB $t" }
    }

    # 9. CloudWatch log groups
    $lgs = Invoke-Aws @('logs','describe-log-groups','--region',$r,'--output','json')
    foreach ($l in $lgs.logGroups) {
        if (Test-IsTarget $l.logGroupName) {
            Write-Hit "LogGroup $($l.logGroupName)"
            Add-Found $r 'log-group' $l.logGroupName $l.logGroupName {
                param($x) Invoke-Aws @('logs','delete-log-group',
                    '--log-group-name',$x.Id,'--region',$x.Region) | Out-Null
            }
        }
    }

    # 10. CloudFormation stacks - LAST, because they own much of the above
    $stacks = Invoke-Aws @('cloudformation','list-stacks','--region',$r,
        '--stack-status-filter','CREATE_COMPLETE','UPDATE_COMPLETE','ROLLBACK_COMPLETE',
        'UPDATE_ROLLBACK_COMPLETE','--output','json')
    foreach ($s in $stacks.StackSummaries) {
        if (Test-IsTarget $s.StackName) {
            Write-Hit "CloudFormation $($s.StackName)"
            Add-Found $r 'cloudformation' $s.StackName $s.StackName {
                param($x) Invoke-Aws @('cloudformation','delete-stack',
                    '--stack-name',$x.Id,'--region',$x.Region) | Out-Null
            }
        } else { Write-Keep "CloudFormation $($s.StackName)" }
    }
}

# 11. S3 - global listing, PROTECTED unless -IncludeS3
Write-Head "S3 buckets (global)"
$buckets = Invoke-Aws @('s3api','list-buckets','--output','json')
foreach ($b in $buckets.Buckets) {
    $isTarget = Test-IsTarget $b.Name
    if ($isTarget -and $IncludeS3) {
        Write-Hit "S3 $($b.Name)"
        Add-Found 'global' 's3-bucket' $b.Name $b.Name {
            param($x)
            Invoke-Aws @('s3','rm',"s3://$($x.Id)",'--recursive') | Out-Null
            Invoke-Aws @('s3api','delete-bucket','--bucket',$x.Id) | Out-Null
        }
    } elseif ($isTarget) {
        Write-Keep "S3 $($b.Name)  (matches a project pattern - pass -IncludeS3 to delete)"
    } else {
        Write-Keep "S3 $($b.Name)"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  REPORT
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "──────────────────────────────────────────────────────────────" -ForegroundColor White
if ($Found.Count -eq 0) {
    Write-Host " Nothing matched the project patterns. Nothing to delete." -ForegroundColor Green
    Write-Host " Anything listed as [keep] above was seen and left alone."
    exit 0
}

Write-Host " $($Found.Count) resource(s) matched the project patterns:" -ForegroundColor Yellow
$Found | Group-Object Kind | Sort-Object Name | ForEach-Object {
    Write-Host ("   {0,-22} {1}" -f $_.Name, $_.Count)
}

if (-not $Delete) {
    Write-Host ""
    Write-Host " INVENTORY ONLY - nothing was deleted." -ForegroundColor Green
    Write-Host " Review the [TARGET] lines above. If they are all things you want gone:"
    Write-Host ""
    Write-Host "     pwsh ./teardown-all.ps1 -Delete" -ForegroundColor White
    if (-not $IncludeS3) {
        Write-Host ""
        Write-Host " S3 buckets were NOT included. Add -IncludeS3 only if you are certain;"
        Write-Host " bucket deletion removes every object in it and cannot be undone."
    }
    exit 0
}

# ── Confirmation before any destructive call ─────────────────────────────────
if (-not $Force) {
    Write-Host ""
    Write-Host " About to DELETE the $($Found.Count) resources listed above." -ForegroundColor Red
    Write-Host " This cannot be undone." -ForegroundColor Red
    $answer = Read-Host " Type DELETE to proceed"
    if ($answer -cne 'DELETE') {
        Write-Host " Cancelled. Nothing was deleted." -ForegroundColor Green
        exit 0
    }
}

# ── Deletion, cheapest-to-lose order already baked into discovery order ──────
Write-Host ""
foreach ($item in $Found) {
    $label = "$($item.Kind) $($item.Name) [$($item.Region)]"
    try {
        & $item.Delete $item
        Write-Done $label
        [void]$Results.Add([pscustomobject]@{ Item = $label; Outcome = 'deleted' })
    } catch {
        Write-Fail "$label - $($_.Exception.Message)"
        [void]$Results.Add([pscustomobject]@{ Item = $label; Outcome = "FAILED: $($_.Exception.Message)" })
    }
}

Write-Host ""
Write-Host "──────────────────────────────────────────────────────────────" -ForegroundColor White
Write-Host " Summary" -ForegroundColor White
$Results | ForEach-Object {
    $colour = if ($_.Outcome -eq 'deleted') { 'Green' } else { 'Red' }
    Write-Host ("   {0,-10} {1}" -f $_.Outcome, $_.Item) -ForegroundColor $colour
}

$failed = @($Results | Where-Object { $_.Outcome -ne 'deleted' })
Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host " $($failed.Count) deletion(s) failed - those resources may still bill." -ForegroundColor Red
    Write-Host " Common causes: a CloudFormation stack still owns the resource (delete the"
    Write-Host " stack first, then re-run), or the resource is still in DELETING state."
} else {
    Write-Host " All matched resources deleted." -ForegroundColor Green
}
Write-Host ""
Write-Host " Re-run without -Delete to confirm the account is clean." -ForegroundColor White
Write-Host " Then check the Billing console after ~24h - it is the only real proof."
