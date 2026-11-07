// This program runs from a pinned NVKE checkout's cli/cmd/astro-inventory
// directory so Go's internal-package boundary permits the command-tree import.
// It constructs the tree without loading site configuration or making API calls.
package main

import (
	"encoding/json"
	"os"
	"sort"

	"github.com/dsx-ai-factory/nvke/cli/internal/nvkectl"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

type flag struct {
	Name      string `json:"name"`
	Type      string `json:"type"`
	Default   string `json:"default"`
	Usage     string `json:"usage"`
	Shorthand string `json:"shorthand,omitempty"`
}

type command struct {
	Path       string   `json:"path"`
	Use        string   `json:"use"`
	Short      string   `json:"short"`
	Aliases    []string `json:"aliases"`
	Executable bool     `json:"executable"`
	Hidden     bool     `json:"hidden"`
	Flags      []flag   `json:"flags"`
}

func main() {
	root := nvkectl.NewRootCmd("inventory", "", "clean", "")
	var commands []command
	var visit func(*cobra.Command)
	visit = func(cmd *cobra.Command) {
		cmd.InitDefaultHelpFlag()
		entry := command{
			Path: cmd.CommandPath(), Use: cmd.Use, Short: cmd.Short,
			Aliases: cmd.Aliases, Executable: cmd.Runnable(), Hidden: cmd.Hidden,
			Flags: []flag{},
		}
		flags := pflag.NewFlagSet("inventory", pflag.ContinueOnError)
		flags.AddFlagSet(cmd.InheritedFlags())
		flags.AddFlagSet(cmd.LocalFlags())
		flags.VisitAll(func(f *pflag.Flag) {
			entry.Flags = append(entry.Flags, flag{
				Name: f.Name, Type: f.Value.Type(), Default: f.DefValue,
				Usage: f.Usage, Shorthand: f.Shorthand,
			})
		})
		commands = append(commands, entry)
		for _, child := range cmd.Commands() {
			visit(child)
		}
	}
	visit(root)
	sort.Slice(commands, func(i, j int) bool { return commands[i].Path < commands[j].Path })
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(commands); err != nil {
		panic(err)
	}
}
