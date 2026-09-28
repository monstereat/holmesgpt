#!/usr/bin/env ruby

require "yaml"

repository_root = File.expand_path("../..", __dir__)
workflow_path = File.join(repository_root, ".github/workflows/publish-aiops-images.yml")
workflow_text = File.read(workflow_path)
workflow = YAML.load_file(workflow_path)
workflow_triggers = workflow["on"] || workflow[true] || {}
workflow_call = workflow_triggers.fetch("workflow_call", {})
publish_job = workflow.fetch("jobs", {}).fetch("publish", {})

abort "The image publisher must not accept a caller-selected environment." if
  workflow_text.include?("release_environment") || workflow_call.fetch("inputs", {}).key?("release_environment")
abort "Registry credentials must come only from the protected GitHub Environment." if workflow_call.key?("secrets")
unless publish_job.fetch("environment", {}) == { "name" => "aiops-image-publish" }
  abort "The image publisher must use the fixed aiops-image-publish environment."
end

puts "Image publisher uses the fixed protected-environment contract."
