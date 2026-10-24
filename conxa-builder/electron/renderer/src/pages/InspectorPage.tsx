import { useCallback, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { errorMessage, fetchSkillPackageFiles, fetchSkillPackageList } from '@/api/workflowApi'
import { buildSkillPackage, fetchWorkflow } from '@/api/workflowsApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { PanelChrome } from '@/components/ui/panel-chrome'
import { StructureTrieRows } from '@/components/skillPackages/StructureTree'
import {
  buildPathTrie,
  defaultSkillPackageActiveKey,
  imageMimeFromKey,
  isImageVisualKey,
  orderedSkillPackageKeys,
} from '@/lib/skillPackageTree'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FolderKanban, Hammer, Loader2 } from 'lucide-react'

/** The demoted home for compiler/packaging internals (bundle_root path, raw
 * package file tree) that used to live on the top-level Packages page and on
 * every default surface. Reachable on demand from a workflow row (Group page)
 * instead of being a wall the user has to climb — see
 * conxa-builder-workflow-redesign.md §3.3/§6 ("View Package" -> Inspector). Also
 * hosts the manual "Rebuild package" escape hatch now that Build Skill Package
 * no longer has its own page (superseded by auto-build-on-sign-off, see
 * cmd_sign_off_workflow).
 *
 * Every workflow's compiled skill lands in the same shared local package
 * directory, so this page finds the one package containing this particular
 * workflow's own compiled skill. "Rebuild package" only recompiles this one
 * workflow (see buildSkillPackage's skillSlug param) — a sibling workflow
 * that isn't ready yet is never touched or required. */
export function InspectorPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const from = searchParams.get('from')
  const [activeFile, setActiveFile] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const qc = useQueryClient()

  const goBack = useCallback(() => navigate(from ?? '/chat'), [navigate, from])

  const workflowQ = useQuery({
    queryKey: ['workflow', workflowId],
    queryFn: () => fetchWorkflow(workflowId!),
    enabled: !!workflowId,
  })
  const workflow = workflowQ.data?.workflow

  const listQ = useQuery({
    queryKey: ['skillPackages'],
    queryFn: fetchSkillPackageList,
    staleTime: 10_000,
  })

  const matchedPackage = useMemo(() => {
    const packages = listQ.data?.packages ?? []
    return packages.find((pkg) => pkg.workflows.some((wf) => wf.workflow_slug === workflow?.slug)) ?? null
  }, [listQ.data, workflow?.slug])

  const filesQ = useQuery({
    queryKey: ['skillPackageFiles', matchedPackage?.package_name],
    queryFn: () => fetchSkillPackageFiles(matchedPackage!.package_name),
    enabled: !!matchedPackage,
  })

  const visibleFiles = useMemo(() => {
    if (filesQ.data) return orderedSkillPackageKeys(Object.keys(filesQ.data.files))
    if (matchedPackage) return orderedSkillPackageKeys(matchedPackage.files)
    return []
  }, [filesQ.data, matchedPackage])
  const trie = useMemo(() => buildPathTrie(visibleFiles), [visibleFiles])
  const resolvedActiveFile = useMemo(() => {
    if (visibleFiles.length === 0) return null
    if (activeFile && visibleFiles.includes(activeFile)) return activeFile
    return defaultSkillPackageActiveKey(visibleFiles)
  }, [activeFile, visibleFiles])
  const activeSource = resolvedActiveFile && filesQ.data ? filesQ.data.files[resolvedActiveFile] ?? '' : ''
  const activeIsImage = resolvedActiveFile ? isImageVisualKey(resolvedActiveFile) : false

  async function handleOpenFolder() {
    if (!matchedPackage) return
    try {
      const bundleRoot = listQ.data?.bundle_root ?? ''
      const sep = bundleRoot.includes('\\') ? '\\' : '/'
      const folderPath =
        matchedPackage.package_path ??
        (bundleRoot
          ? `${bundleRoot}${sep}${matchedPackage.package_folder ?? matchedPackage.package_name}`
          : matchedPackage.package_folder ?? matchedPackage.package_name)
      await window.conxa.openExternal(`file:///${folderPath.replace(/\\/g, '/')}`)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not open package folder.'))
    }
  }

  async function handleRebuild() {
    if (!workflow) return
    setRebuilding(true)
    try {
      await buildSkillPackage(workflow.slug)
      toast.success('Package rebuilt.')
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['skillPackages'] }),
        qc.invalidateQueries({ queryKey: ['skillPackageFiles', matchedPackage?.package_name] }),
        qc.invalidateQueries({ queryKey: ['workflow', workflow.id] }),
      ])
    } catch (err) {
      toast.error(errorMessage(err, 'Could not rebuild the package.'))
    } finally {
      setRebuilding(false)
    }
  }

  const loading = workflowQ.isLoading || listQ.isLoading
  const error = (workflowQ.error ?? listQ.error) as Error | null

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title={`Inspector — ${workflow?.name ?? 'workflow'}`}
        description="Package files and internals, for reference — not part of the everyday flow."
        onBack={goBack}
        leading={
          listQ.data ? (
            <Badge variant="outline" className="max-w-full truncate border-white/10 bg-white/[0.04] font-mono text-[11px] text-zinc-400">
              {listQ.data.bundle_root}
            </Badge>
          ) : undefined
        }
        actions={
          <>
            <Button size="sm" variant="outline" className="border-white/10 bg-white/[0.04] text-zinc-200" onClick={() => void handleOpenFolder()} disabled={!matchedPackage}>
              <FolderKanban className="size-3.5" /> Open in Explorer
            </Button>
            <Button size="sm" variant="outline" className="border-white/10 bg-white/[0.04] text-zinc-200" onClick={() => void handleRebuild()} disabled={rebuilding || !workflow}>
              {rebuilding ? <Loader2 className="size-3.5 animate-spin" /> : <Hammer className="size-3.5" />} Rebuild package
            </Button>
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        {loading ? (
          <p className="p-6 text-sm text-zinc-500">Loading…</p>
        ) : error ? (
          <p className="p-6 text-sm text-red-400">{error.message ?? 'Could not load packages.'}</p>
        ) : !matchedPackage ? (
          <p className="p-6 text-sm text-zinc-500">No built package yet for this automation.</p>
        ) : (
          <>
            <PanelChrome className="w-64 shrink-0 overflow-y-auto rounded-none border-y-0 border-l-0 p-2">
              <StructureTrieRows nodes={trie.children} depth={0} pathPrefix="" activeFile={resolvedActiveFile} onPick={setActiveFile} />
            </PanelChrome>
            <div className="min-w-0 flex-1 overflow-y-auto p-4">
              {!resolvedActiveFile ? (
                <p className="text-sm text-zinc-500">Select a file.</p>
              ) : activeIsImage ? (
                <img
                  src={`data:${imageMimeFromKey(resolvedActiveFile)};base64,${activeSource}`}
                  alt={resolvedActiveFile}
                  className="max-w-full rounded-lg border border-white/10"
                />
              ) : (
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-zinc-100">{activeSource}</pre>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
