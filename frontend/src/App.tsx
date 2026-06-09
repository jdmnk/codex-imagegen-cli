import * as React from "react"
import {
  Clipboard,
  Copy,
  FileImage,
  FolderOpen,
  ImagePlus,
  Layers,
  Paintbrush,
  Play,
  Settings2,
  Sparkles,
  Terminal,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

type Mode = "generate" | "edit" | "batch"
type Size = "auto" | "1024x1024" | "1536x1024" | "1024x1536"
type Quality = "auto" | "low" | "medium" | "high"
type Background = "auto" | "transparent" | "opaque"
type OutputFormat = "auto" | "png" | "webp"

type PreviewFile = {
  name: string
  url: string
}

const sizes: Size[] = ["auto", "1024x1024", "1536x1024", "1024x1536"]
const qualities: Quality[] = ["auto", "low", "medium", "high"]
const backgrounds: Background[] = ["auto", "transparent", "opaque"]
const outputFormats: OutputFormat[] = ["auto", "png", "webp"]

const shellQuote = (value: string) => `'${value.replace(/'/g, "'\"'\"'")}'`

function App() {
  const [mode, setMode] = React.useState<Mode>("edit")
  const [prompt, setPrompt] = React.useState("make the look and colors more professional without excessive corrections")
  const [subjectPath, setSubjectPath] = React.useState("/path/to/source.png")
  const [extraImagePaths, setExtraImagePaths] = React.useState("")
  const [stylePath, setStylePath] = React.useState("")
  const [batchInput, setBatchInput] = React.useState("jobs.jsonl")
  const [batchOutDir, setBatchOutDir] = React.useState("output/imagegen")
  const [outputPath, setOutputPath] = React.useState("output/result.webp")
  const [model, setModel] = React.useState("")
  const [workingDirectory, setWorkingDirectory] = React.useState("")
  const [size, setSize] = React.useState<Size>("auto")
  const [quality, setQuality] = React.useState<Quality>("auto")
  const [background, setBackground] = React.useState<Background>("auto")
  const [outputFormat, setOutputFormat] = React.useState<OutputFormat>("auto")
  const [count, setCount] = React.useState(1)
  const [webpQuality, setWebpQuality] = React.useState(85)
  const [inputMaxEdge, setInputMaxEdge] = React.useState(1536)
  const [inputWebpQuality, setInputWebpQuality] = React.useState(90)
  const [force, setForce] = React.useState(false)
  const [dryRun, setDryRun] = React.useState(false)
  const [failFast, setFailFast] = React.useState(false)
  const [copied, setCopied] = React.useState(false)

  const command = React.useMemo(() => {
    const parts = ["codex-imagegen", mode]

    if (mode === "generate") {
      if (prompt.trim()) parts.push("--prompt", shellQuote(prompt.trim()))
      if (outputPath.trim()) parts.push("--out", shellQuote(outputPath.trim()))
    }

    if (mode === "edit") {
      for (const imagePath of imagePathsFrom(subjectPath, extraImagePaths)) {
        parts.push("--image", shellQuote(imagePath))
      }
      if (stylePath.trim()) parts.push("--style-image", shellQuote(stylePath.trim()))
      if (prompt.trim()) parts.push("--prompt", shellQuote(prompt.trim()))
      if (outputPath.trim()) parts.push("--out", shellQuote(outputPath.trim()))
    }

    if (mode === "batch") {
      if (batchInput.trim()) parts.push("--input", shellQuote(batchInput.trim()))
      if (batchOutDir.trim()) parts.push("--out-dir", shellQuote(batchOutDir.trim()))
      if (failFast) parts.push("--fail-fast")
    }

    parts.push("--size", size, "--quality", quality, "--background", background)
    if (outputFormat !== "auto") parts.push("--output-format", outputFormat)
    if (count > 1) parts.push("--n", String(count))
    if (webpQuality !== 85) parts.push("--webp-quality", String(webpQuality))
    if (inputMaxEdge !== 1536) parts.push("--input-max-edge", String(inputMaxEdge))
    if (inputWebpQuality !== 90) parts.push("--input-webp-quality", String(inputWebpQuality))
    if (model.trim()) parts.push("--model", shellQuote(model.trim()))
    if (workingDirectory.trim()) parts.push("--cd", shellQuote(workingDirectory.trim()))
    if (force && mode !== "batch") parts.push("--force")
    if (dryRun) parts.push("--dry-run")

    return parts.join(" ")
  }, [
    background,
    batchInput,
    batchOutDir,
    count,
    dryRun,
    extraImagePaths,
    failFast,
    force,
    inputMaxEdge,
    inputWebpQuality,
    mode,
    model,
    outputFormat,
    outputPath,
    prompt,
    quality,
    size,
    stylePath,
    subjectPath,
    webpQuality,
    workingDirectory,
  ])

  const validation = React.useMemo(() => validateCommand(mode, prompt, subjectPath, stylePath, outputPath, batchInput, batchOutDir), [
    batchInput,
    batchOutDir,
    mode,
    outputPath,
    prompt,
    stylePath,
    subjectPath,
  ])

  async function copyCommand() {
    await navigator.clipboard.writeText(command)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }

  return (
    <TooltipProvider>
      <main className="min-h-screen bg-background text-foreground">
        <div className="mx-auto flex min-h-screen w-full max-w-[1240px] flex-col px-4 py-4 sm:px-6">
          <header className="flex min-h-14 items-center justify-between border-b">
            <div className="flex items-center gap-3">
              <div className="flex size-8 items-center justify-center rounded-md border bg-primary text-primary-foreground">
                <Terminal className="size-4" />
              </div>
              <div>
                <h1 className="text-base font-semibold">Codex Imagegen</h1>
                <p className="text-sm text-muted-foreground">Build runnable CLI commands.</p>
              </div>
            </div>
            <Button onClick={copyCommand} disabled={validation.length > 0}>
              <Copy />
              {copied ? "Copied" : "Copy command"}
            </Button>
          </header>

          <div className="grid flex-1 gap-5 py-5 lg:grid-cols-[minmax(0,760px)_360px] lg:justify-center lg:items-start">
            <div className="min-w-0 space-y-5">
              <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
                <div className="flex items-center justify-between gap-3">
                  <TabsList>
                    <TabsTrigger value="generate">
                      <Sparkles className="mr-2 size-4" />
                      Generate
                    </TabsTrigger>
                    <TabsTrigger value="edit">
                      <ImagePlus className="mr-2 size-4" />
                      Edit
                    </TabsTrigger>
                    <TabsTrigger value="batch">
                      <Layers className="mr-2 size-4" />
                      Batch
                    </TabsTrigger>
                  </TabsList>
                </div>

                <TabsContent value="generate">
                  <Panel title="Generate" icon={Sparkles} tone="violet">
                    <PromptField value={prompt} onChange={setPrompt} placeholder="Describe the image to generate." />
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px]">
                      <PathField label="Output path" value={outputPath} onChange={setOutputPath} placeholder="output/result.webp" />
                      <NumberField label="Outputs" value={count} min={1} max={8} onChange={setCount} />
                    </div>
                  </Panel>
                </TabsContent>

                <TabsContent value="edit">
                  <Panel title="Edit" icon={Paintbrush} tone="green">
                    <div className="grid gap-3 md:grid-cols-2">
                      <ImageReference
                        label="Content image"
                        path={subjectPath}
                        onPathChange={setSubjectPath}
                        placeholder="/Users/me/project/input.png"
                      />
                      <ImageReference
                        label="Style image"
                        path={stylePath}
                        onPathChange={setStylePath}
                        placeholder="/Users/me/project/style.png"
                      />
                    </div>
                    <PromptField value={prompt} onChange={setPrompt} placeholder="Optional when a style image is set." compact />
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px]">
                      <PathField label="Output path" value={outputPath} onChange={setOutputPath} placeholder="output/result.webp" />
                      <NumberField label="Outputs" value={count} min={1} max={8} onChange={setCount} />
                    </div>
                    <PathField
                      label="More edit images"
                      value={extraImagePaths}
                      onChange={setExtraImagePaths}
                      placeholder="/path/detail.png, /path/mask.png"
                    />
                  </Panel>
                </TabsContent>

                <TabsContent value="batch">
                  <Panel title="Batch" icon={Clipboard} tone="orange">
                    <div className="grid gap-3 md:grid-cols-2">
                      <PathField label="JSONL input" value={batchInput} onChange={setBatchInput} placeholder="jobs.jsonl" />
                      <PathField label="Output directory" value={batchOutDir} onChange={setBatchOutDir} placeholder="output/imagegen" />
                    </div>
                    <OptionRow label="Fail fast" description="Stop on the first failed job." checked={failFast} onCheckedChange={setFailFast} />
                  </Panel>
                </TabsContent>
              </Tabs>

              <div className="grid gap-5 md:grid-cols-2">
                <Panel title="Image" icon={Settings2} compact tone="teal">
                  <div className="grid gap-3 sm:grid-cols-3 md:grid-cols-1 xl:grid-cols-3">
                    <SelectField label="Size" value={size} onChange={(value) => setSize(value as Size)} options={sizes} />
                    <SelectField label="Quality" value={quality} onChange={(value) => setQuality(value as Quality)} options={qualities} />
                    <SelectField
                      label="Background"
                      value={background}
                      onChange={(value) => setBackground(value as Background)}
                      options={backgrounds}
                    />
                  </div>
                  {mode === "edit" ? (
                    <div className="grid gap-3 sm:grid-cols-[140px_minmax(0,1fr)] sm:items-end">
                      <NumberField label="Input max edge" value={inputMaxEdge} min={0} max={4096} onChange={setInputMaxEdge} />
                      <SliderField label="Input WebP quality" value={inputWebpQuality} onChange={setInputWebpQuality} compact />
                    </div>
                  ) : null}
                </Panel>

                <Panel title="Output" icon={FolderOpen} compact tone="orange">
                  <div className="grid gap-3 sm:grid-cols-[1fr_1fr] md:grid-cols-1 xl:grid-cols-[1fr_1fr]">
                    <SelectField
                      label="Output format"
                      value={outputFormat}
                      onChange={(value) => setOutputFormat(value as OutputFormat)}
                      options={outputFormats}
                    />
                    <SliderField label="WebP quality" value={webpQuality} onChange={setWebpQuality} compact />
                  </div>
                </Panel>
              </div>

              <Panel title="Run" icon={Play} compact>
                <div className="grid gap-3 md:grid-cols-2">
                    <PathField label="Working directory" value={workingDirectory} onChange={setWorkingDirectory} placeholder="/Users/me/project" />
                    <PathField label="Model override" value={model} onChange={setModel} placeholder="gpt-5.5" />
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <OptionRow label="Dry run" description="Print request shape without contacting Codex." checked={dryRun} onCheckedChange={setDryRun} />
                  <OptionRow label="Force overwrite" description="Allow replacing existing output files." checked={force} onCheckedChange={setForce} />
                </div>
              </Panel>
            </div>

            <aside className="space-y-5 lg:sticky lg:top-5">
              <Panel title="Command" icon={Terminal} tone="violet">
                <pre className="max-h-[320px] min-h-[160px] overflow-auto rounded-md border bg-code p-3 text-[13px] leading-5 text-code-foreground">
                  <code>{command}</code>
                </pre>
                {validation.length > 0 ? (
                  <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning-foreground">
                    {validation[0]}
                  </div>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={copyCommand} disabled={validation.length > 0}>
                    <Copy />
                    {copied ? "Copied" : "Copy"}
                  </Button>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="outline" type="button">
                        <Terminal />
                        Terminal
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Paste the copied command into your shell.</TooltipContent>
                  </Tooltip>
                </div>
              </Panel>

              <Panel title="Summary" icon={FolderOpen} compact>
                <dl className="grid grid-cols-[92px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
                  <dt className="text-muted-foreground">Mode</dt>
                  <dd className="font-medium">{mode}</dd>
                  <dt className="text-muted-foreground">Backend</dt>
                  <dd className="font-medium">direct</dd>
                  <dt className="text-muted-foreground">Output</dt>
                  <dd className="truncate font-medium">{mode === "batch" ? batchOutDir : outputPath}</dd>
                  <dt className="text-muted-foreground">Images</dt>
                  <dd className="font-medium">{mode === "edit" ? imagePathsFrom(subjectPath, extraImagePaths).length + (stylePath ? 1 : 0) : 0}</dd>
                </dl>
              </Panel>
            </aside>
          </div>
        </div>
      </main>
    </TooltipProvider>
  )
}

function Panel({
  title,
  icon: Icon,
  children,
  compact = false,
  tone,
}: {
  title: string
  icon: React.ElementType
  children: React.ReactNode
  compact?: boolean
  tone?: "green" | "orange" | "teal" | "violet"
}) {
  return (
    <section className="rounded-lg border bg-card text-card-foreground shadow-sm">
      <div className="flex min-h-11 items-center gap-3 border-b px-3.5">
        <span className={cn("flex size-6 items-center justify-center rounded-md border", toneClass(tone))}>
          <Icon className="size-3.5" />
        </span>
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className={cn("space-y-4", compact ? "p-3.5" : "p-4")}>{children}</div>
    </section>
  )
}

function toneClass(tone?: "green" | "orange" | "teal" | "violet") {
  if (tone === "green") return "border-green-200 bg-green-50 text-green-700"
  if (tone === "orange") return "border-orange-200 bg-orange-50 text-orange-700"
  if (tone === "teal") return "border-teal-200 bg-teal-50 text-teal-700"
  if (tone === "violet") return "border-violet-200 bg-violet-50 text-violet-700"
  return "border-border bg-muted text-muted-foreground"
}

function PromptField({
  value,
  onChange,
  placeholder,
  compact = false,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  compact?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor="prompt">Prompt</Label>
      <Textarea
        id="prompt"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={compact ? "min-h-[76px]" : undefined}
      />
    </div>
  )
}

function PathField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  const id = React.useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </div>
  )
}

function ImageReference({
  label,
  path,
  onPathChange,
  placeholder,
}: {
  label: string
  path: string
  onPathChange: (value: string) => void
  placeholder: string
}) {
  const [preview, setPreview] = React.useState<PreviewFile | null>(null)

  React.useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview.url)
    }
  }, [preview])

  function onFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    if (preview) URL.revokeObjectURL(preview.url)
    setPreview({ name: file.name, url: URL.createObjectURL(file) })
    if (!path.trim()) onPathChange(file.name)
  }

  return (
    <div className="space-y-3">
      <PathField label={label} value={path} onChange={onPathChange} placeholder={placeholder} />
      <label className="flex h-32 cursor-pointer items-center justify-center overflow-hidden rounded-md border bg-muted text-sm text-muted-foreground">
        {preview ? (
          <img src={preview.url} alt={preview.name} className="size-full object-cover" />
        ) : (
          <span className="flex items-center gap-2">
            <FileImage className="size-4" />
            Pick preview
          </span>
        )}
        <input className="sr-only" type="file" accept="image/*" onChange={onFileChange} />
      </label>
    </div>
  )
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: string[]
}) {
  const id = React.useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
}) {
  const id = React.useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(clamp(Number(event.target.value), min, max))}
      />
    </div>
  )
}

function SliderField({
  label,
  value,
  onChange,
  compact = false,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  compact?: boolean
}) {
  if (compact) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <Label>{label}</Label>
          <span className="text-sm text-muted-foreground">{value}</span>
        </div>
        <Slider value={[value]} min={1} max={100} step={1} onValueChange={(next) => onChange(next[0] ?? value)} />
      </div>
    )
  }

  return (
    <div className="grid gap-3 md:grid-cols-[180px_minmax(0,1fr)_48px] md:items-center">
      <Label>{label}</Label>
      <Slider value={[value]} min={1} max={100} step={1} onValueChange={(next) => onChange(next[0] ?? value)} />
      <span className="text-right text-sm text-muted-foreground">{value}</span>
    </div>
  )
}

function OptionRow({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  const id = React.useId()
  return (
    <label htmlFor={id} className="flex gap-3 rounded-md border bg-card p-3">
      <Checkbox id={id} checked={checked} onCheckedChange={(value) => onCheckedChange(value === true)} />
      <span className="grid gap-1">
        <span className="text-sm font-medium leading-none">{label}</span>
        <span className="text-sm text-muted-foreground">{description}</span>
      </span>
    </label>
  )
}

function imagePathsFrom(subjectPath: string, extraImagePaths: string) {
  return [subjectPath, ...extraImagePaths.split(",")].map((value) => value.trim()).filter(Boolean)
}

function validateCommand(
  mode: Mode,
  prompt: string,
  subjectPath: string,
  stylePath: string,
  outputPath: string,
  batchInput: string,
  batchOutDir: string,
) {
  const issues: string[] = []
  if (mode === "generate" && !prompt.trim()) issues.push("Add a prompt before copying the generate command.")
  if (mode === "edit" && !subjectPath.trim()) issues.push("Add at least one content image path.")
  if (mode === "edit" && !stylePath.trim() && !prompt.trim()) issues.push("Add a prompt or a style image for edit mode.")
  if (mode !== "batch" && !outputPath.trim()) issues.push("Add an output path.")
  if (mode === "batch" && !batchInput.trim()) issues.push("Add a JSONL input path.")
  if (mode === "batch" && !batchOutDir.trim()) issues.push("Add a batch output directory.")
  return issues
}

function clamp(value: number, min: number, max: number) {
  if (Number.isNaN(value)) return min
  return Math.min(max, Math.max(min, value))
}

export default App
