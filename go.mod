module afvi-ai

go 1.27

require pxspectool v0.0.0

// The production server checks out PxSpecTool beside AFVI_AI at ~/dev/PxSpecTool.
replace pxspectool => ../PxSpecTool
