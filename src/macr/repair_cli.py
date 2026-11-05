"""Shared repair argument definitions; no provider, evaluator or runtime imports."""
from pathlib import Path


def add_repair_parser(subparsers):
    parser = subparsers.add_parser('repair', help='Bounded source repair; remote transport disabled, missing validation stops honestly.')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--task', required=True, type=Path, help='Strict neutral task JSON; never a scoring or private-answer path.')
    parser.add_argument('--model-config', required=True, type=Path, help='Nonsecret shared model, limits and declared price schedule.')
    parser.add_argument('--out', required=True, type=Path, help='New exclusive local record directory.')
    parser.add_argument('--strategy', choices=('direct_api','api_test_feedback','api_debugger'), default='api_debugger')
    parser.add_argument('--proposal-contract', choices=('v1','v2','v3','v4','v5','v6'), default='v1',
                        help='v2 retains candidates; v3 shares public outcomes; v4 adds public failure diagnostics; v5 adds history; v6 can select a saved candidate.')
    parser.add_argument('--enable-remote',action='store_true',help='Explicit official HTTP mode; requires v2/v3/v4/v5/v6 and a durable spend plan/cell.')
    parser.add_argument('--spend-plan',type=Path,help='Nonsecret frozen spend plan; never a credential file.')
    parser.add_argument('--spend-cell',help='One exact planned cell; replay in another output directory is forbidden.')


def add_localize_parser(subparsers):
    parser=subparsers.add_parser('repair-locate',help='Propose source files from an explicitly authorized discovery root; no model, edits or test execution.')
    parser.add_argument('--repo',type=Path,required=True,help='Separate authorized local discovery root; private scoring materials must be excluded before reading.')
    parser.add_argument('--issue-file',type=Path,required=True,help='UTF-8 issue text, at most 2048 bytes; treated as data.')
    parser.add_argument('--exclude',action='append',default=[],help='Additional scope exclusion, applied before source content is read.')
    parser.add_argument('--max-candidates',type=int,default=8)
    parser.add_argument('--windowed-input',action='store_true',help='Additionally propose explicit partial line windows over complete large originals; no task or execution authority.')
    parser.add_argument('--out',type=Path,required=True,help='New private directory outside the source root; retains the discovery snapshot and index.')


def add_comparison_parser(subparsers):
    parser = subparsers.add_parser('repair-comparison', help='Local frozen repair plans and full-denominator reports; never dispatches calls or tests.')
    commands = parser.add_subparsers(dest='comparison_operation', required=True)
    compile_parser = commands.add_parser('compile')
    compile_parser.add_argument('--spec', type=Path, required=True, help='Explicit nonsecret comparison specification; no private scoring files are opened.')
    report_parser = commands.add_parser('report')
    report_parser.add_argument('--plan', type=Path, required=True)
    report_parser.add_argument('--results', type=Path, required=True, help='Sanitized result map by cell ID; omit missing cells, never invent passes.')
    handoff_parser = commands.add_parser('handoff', help='Revalidate one saved repair record and export a pending, unscored packet.')
    handoff_parser.add_argument('--plan', type=Path, required=True)
    handoff_parser.add_argument('--record', type=Path, required=True)
    handoff_parser.add_argument('--cell', required=True)
    scoring_parser = commands.add_parser('stage-scoring', help='Stage committed, whole evaluator tests over a revalidated record; never executes or scores.')
    scoring_parser.add_argument('--plan', type=Path, required=True)
    scoring_parser.add_argument('--record', type=Path, required=True)
    scoring_parser.add_argument('--cell', required=True)
    scoring_parser.add_argument('--overlay-manifest', type=Path, required=True)
    scoring_parser.add_argument('--overlay-root', type=Path, required=True)
    for command in (compile_parser, report_parser, handoff_parser, scoring_parser):
        command.add_argument('--out', type=Path, required=True, help='New exclusive local artifact directory; never overwritten or resumed.')


def add_inventory_parser(subparsers):
    inventory_parser = subparsers.add_parser('repair-benchmark-inventory',
        help='Inventory every BugsInPy metadata entry and optionally check neutral repair inputs; no execution.')
    inventory_parser.add_argument('--index', required=True, type=Path,
        help='Local pinned GitHub recursive tree JSON; no patch or benchmark checkout execution.')
    inventory_parser.add_argument('--revision', required=True, help='Exact upstream commit, distinct from the tree object SHA.')
    inventory_parser.add_argument('--task-map', type=Path,
        help='Optional JSON mapping project/bug to explicit neutral repo/task paths.')
    inventory_parser.add_argument('--out', required=True, type=Path, help='New exclusive local JSON report file.')


def add_parsers(subparsers):
    add_repair_parser(subparsers)
    add_localize_parser(subparsers)
    add_comparison_parser(subparsers)
    add_inventory_parser(subparsers)
