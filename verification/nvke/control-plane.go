// Run from the pinned upstream management-engine/cmd/astro-envtest directory.
// This starts only local etcd and kube-apiserver processes, with the real NVKE
// CRD schemas/admission. It does not provision nodes or run lifecycle controllers.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"k8s.io/client-go/tools/clientcmd"
	clientcmdapi "k8s.io/client-go/tools/clientcmd/api"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

func main() {
	if len(os.Args) != 4 {
		panic("usage: astro-envtest <binary-assets-dir> <nvke-crd-dir> <kubeconfig-output>")
	}
	environment := &envtest.Environment{
		BinaryAssetsDirectory: os.Args[1],
		CRDDirectoryPaths:     []string{os.Args[2]},
		ErrorIfCRDPathMissing: true,
	}
	config, err := environment.Start()
	if err != nil {
		panic(err)
	}
	defer func() {
		if err := environment.Stop(); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
	}()
	output := clientcmdapi.NewConfig()
	output.Clusters["astro-nvke-local"] = &clientcmdapi.Cluster{
		Server: config.Host, CertificateAuthorityData: config.CAData,
	}
	output.AuthInfos["astro-nvke-local"] = &clientcmdapi.AuthInfo{
		ClientCertificateData: config.CertData, ClientKeyData: config.KeyData,
	}
	output.Contexts["astro-nvke-local"] = &clientcmdapi.Context{
		Cluster: "astro-nvke-local", AuthInfo: "astro-nvke-local",
	}
	output.CurrentContext = "astro-nvke-local"
	path, err := filepath.Abs(os.Args[3])
	if err != nil {
		panic(err)
	}
	if err := clientcmd.WriteToFile(*output, path); err != nil {
		panic(err)
	}
	if err := os.Chmod(path, 0600); err != nil {
		panic(err)
	}
	fmt.Println("Local NVKE admission control plane ready; kubeconfig:", path)
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop
}
